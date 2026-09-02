"""ai-gateway — the server behind ATHERA Agent.

custom_ai_bridge and custom_ai_features are installed in every tenant and have
been calling http://ai-gateway:8080 since ADR 0002 imported them, into nothing.
This is that server. The routes and body shapes come from
custom_ai_bridge/models/custom_ai.py and custom_ai_features/models/
ai_gateway_extra.py; they were not designed here.

HOW "KNOWLEDGE LIMITED TO THE CLIENT'S OWN DATABASE" IS ENFORCED.

Not by prompt. Four structural properties, in order of how much they matter:

1. THIS SERVICE NEVER READS A DATABASE. It has no DSN, no driver, and no
   network route to Postgres. Whatever it were persuaded to say, it cannot
   fetch a row.

2. /v1/workflow/nlq RETURNS AN ODOO DOMAIN, NOT SQL. The response is a
   {model, domain, fields, order, limit} plan, and Odoo executes it through the
   ORM -- under that user's record rules, that tenant's database, and the PDP
   masking rules already installed there. A generated SELECT would bypass all
   three. This is the single most important line in the file.

3. THE SCHEMA IS SUPPLIED, NOT DISCOVERED. `schema_hint` comes from the
   caller, which builds it from the models that user may read. The model cannot
   name a table it was not shown, and the response schema is enforced by the
   API rather than requested politely.

4. THE PLAN IS VALIDATED AGAINST THAT SCHEMA HERE. A model or field outside
   schema_hint is rejected before the response leaves this process, so a
   hallucinated table never reaches the ORM.

The gateway is stateless: no conversation store, no cache keyed by tenant,
nothing that could serve one tenant's text to another. X-Tenant-Id is recorded
in metrics and logs only.
"""

from __future__ import annotations

import json
import logging
import os
import re

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from .providers import ModelRefused, ProviderError, build_provider
from .security import HMACMiddleware

logger = logging.getLogger("ai_gateway")

CALLS = Counter(
    "athera_agent_calls_total",
    "Agent calls, by route, tenant and outcome.",
    ["route", "tenant", "outcome"],
)

MODEL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")

#: The shape /v1/workflow/nlq must answer in. Enforced by the API through
#: output_config.format, then re-checked against schema_hint below -- a schema
#: the provider enforces is still only a shape, not a permission.
NLQ_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["model", "domain", "fields", "limit", "rationale"],
    "properties": {
        "model": {"type": "string"},
        "domain": {"type": "array"},
        "fields": {"type": "array", "items": {"type": "string"}},
        "order": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "rationale": {"type": "string"},
        "follow_up": {"type": ["string", "null"]},
    },
}

NLQ_SYSTEM = """You translate a business question into an Odoo search plan.

Answer with a plan only. You are given the models and fields the asker is
allowed to read; use nothing else, and never invent a model or a field name.

`domain` is an Odoo domain: a list of [field, operator, value] triples, with
'&' / '|' prefix operators where needed. It is NOT SQL, and you must not
produce SQL.

If the question cannot be answered from the models offered, say so in
`rationale` and return an empty domain rather than guessing at a model that
was not listed."""


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_plan(plan: dict, schema_hint: list) -> str | None:
    """Reject a plan that names anything outside the caller's own schema.

    Returns an error string, or None when the plan is inside the fence. This is
    property 4 in the module docstring: the API enforces the SHAPE of the
    answer, and this enforces its CONTENT against what the asker may see.
    """
    allowed = {}
    for entry in schema_hint or []:
        if isinstance(entry, dict) and entry.get("model"):
            allowed[entry["model"]] = set(entry.get("fields") or [])

    model = plan.get("model")
    if not model:
        return None  # an empty plan is a refusal to guess, which is allowed
    if model not in allowed:
        return "plan names model %r, which was not offered to it" % model

    unknown = [f for f in (plan.get("fields") or []) if f not in allowed[model]]
    if unknown:
        return "plan names fields not offered for %s: %s" % (model, ", ".join(sorted(unknown)))

    # Domain leaves are [field, op, value]; the field must also be in the fence.
    for leaf in plan.get("domain") or []:
        if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
            field = str(leaf[0]).split(".", 1)[0]
            if field not in allowed[model]:
                return "domain filters on %r, which was not offered for %s" % (field, model)
    return None


def create_app() -> FastAPI:
    secret = os.environ.get("GATEWAY_SHARED_SECRET", "")
    if len(secret) < 32 or "changeme" in secret:
        # Refuse to start rather than run an HMAC boundary a guess could cross.
        raise RuntimeError("GATEWAY_SHARED_SECRET must be at least 32 real characters.")

    provider = build_provider()
    app = FastAPI(title="ATHERA Agent gateway", docs_url=None, redoc_url=None)
    app.add_middleware(
        HMACMiddleware,
        secret=secret,
        window_seconds=int(os.environ.get("AI_GATEWAY_HMAC_WINDOW_SECONDS", "300")),
    )
    router = APIRouter()

    @router.get("/healthz")
    @router.get("/health")
    def health():
        return {"status": "ok", "provider": os.environ.get("AI_GATEWAY_PROVIDER", "anthropic")}

    @router.get("/metrics")
    def metrics():
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    def _tenant(request: Request) -> str:
        return getattr(request.state, "tenant", "unknown")

    @router.post("/v1/chat")
    async def chat(request: Request):
        body = await _body(request)
        tenant = _tenant(request)
        messages = body.get("messages") or []
        if not messages:
            CALLS.labels("chat", tenant, "rejected").inc()
            return _err(400, "invalid_request", "messages must not be empty.")
        try:
            out = provider.complete(
                system=body.get("system"),
                messages=messages,
                max_tokens=int(body.get("max_tokens") or 4096),
                temperature=body.get("temperature"),
            )
        except ModelRefused as exc:
            CALLS.labels("chat", tenant, "refused").inc()
            return _err(422, "model_refused", "The model declined this request (%s)." % exc)
        except ProviderError as exc:
            CALLS.labels("chat", tenant, "error").inc()
            logger.error("chat failed tenant=%s: %s", tenant, exc)
            return _err(502, "upstream_unavailable", str(exc)[:300])
        CALLS.labels("chat", tenant, "success").inc()
        return {"text": out["text"], "model": out["model"], "usage": out["usage"]}

    @router.post("/v1/workflow/nlq")
    async def nlq(request: Request):
        body = await _body(request)
        tenant = _tenant(request)
        question = (body.get("question") or "").strip()
        schema_hint = body.get("schema_hint") or []
        if not question:
            CALLS.labels("nlq", tenant, "rejected").inc()
            return _err(400, "invalid_request", "question must not be empty.")
        if not schema_hint:
            # Without a fence there is nothing to keep the answer inside, so
            # this is a refusal rather than an open-ended query.
            CALLS.labels("nlq", tenant, "rejected").inc()
            return _err(400, "invalid_request", "schema_hint must not be empty.")

        schema_text = "\n".join(
            "- %s: %s%s" % (
                e.get("model"), ", ".join(e.get("fields") or []),
                "  (%s)" % e["description"] if e.get("description") else "",
            )
            for e in schema_hint if isinstance(e, dict)
        )
        user = (
            "Locale: %s\nThe asker may see PII: %s\n\nModels and fields available:\n%s\n\n"
            "Question:\n%s\n" % (
                body.get("locale") or "id_ID",
                bool(body.get("user_can_view_pii")),
                schema_text, question,
            )
        )
        try:
            out = provider.complete(
                system=NLQ_SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=int(body.get("max_tokens") or 2000),
                schema=NLQ_SCHEMA,
            )
        except ModelRefused as exc:
            CALLS.labels("nlq", tenant, "refused").inc()
            return _err(422, "model_refused", "The model declined this request (%s)." % exc)
        except ProviderError as exc:
            CALLS.labels("nlq", tenant, "error").inc()
            logger.error("nlq failed tenant=%s: %s", tenant, exc)
            return _err(502, "upstream_unavailable", str(exc)[:300])

        try:
            plan = json.loads(out["text"])
        except ValueError:
            CALLS.labels("nlq", tenant, "unparseable").inc()
            return _err(502, "bad_plan", "The model did not return a parseable plan.")

        fence = _validate_plan(plan, schema_hint)
        if fence:
            # The one place a leak would show up, so it is a hard refusal and
            # it is counted separately -- a rising `out_of_scope` is a signal,
            # not noise.
            CALLS.labels("nlq", tenant, "out_of_scope").inc()
            logger.warning("nlq plan rejected tenant=%s: %s", tenant, fence)
            return _err(422, "out_of_scope", fence)

        CALLS.labels("nlq", tenant, "success").inc()
        plan["model_used"] = out["model"]
        return plan

    @router.post("/v1/workflow/recommend")
    async def recommend(request: Request):
        body = await _body(request)
        tenant = _tenant(request)
        model = body.get("model") or ""
        if not MODEL_NAME_RE.match(model):
            CALLS.labels("recommend", tenant, "rejected").inc()
            return _err(400, "invalid_request", "model must be an Odoo model name.")
        user = (
            "Record: %s (id %s)\nLocale: %s\n\nFields:\n%s\n\n"
            "Give a short, concrete recommendation for what to do with this record."
            % (model, body.get("res_id"), body.get("locale") or "id_ID",
               json.dumps(body.get("payload") or {}, ensure_ascii=False, default=str)[:8000])
        )
        try:
            out = provider.complete(
                system="You advise on a single business record. Be specific and brief. "
                       "Recommend an action, and say what in the record supports it.",
                messages=[{"role": "user", "content": user}],
                max_tokens=int(body.get("max_tokens") or 1500),
            )
        except ModelRefused as exc:
            CALLS.labels("recommend", tenant, "refused").inc()
            return _err(422, "model_refused", "The model declined this request (%s)." % exc)
        except ProviderError as exc:
            CALLS.labels("recommend", tenant, "error").inc()
            return _err(502, "upstream_unavailable", str(exc)[:300])
        CALLS.labels("recommend", tenant, "success").inc()
        return {"recommendation": out["text"], "model": out["model"], "usage": out["usage"]}

    @router.post("/v1/workflow/anomaly", status_code=501)
    @router.post("/v1/workflow/classify", status_code=501)
    async def unimplemented(request: Request):
        """custom_ai_features calls these; they are not built yet.

        501 rather than a plausible-looking empty result. An anomaly scan that
        silently returns "no anomalies" is worse than one that is honestly
        absent -- it would be believed.
        """
        return _err(501, "not_implemented",
                    "This route is not built yet. /v1/chat, /v1/workflow/nlq and "
                    "/v1/workflow/recommend are.")

    app.include_router(router)
    return app
