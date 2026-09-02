"""The model call, behind one interface.

Two providers. `anthropic` is the production path; `ollama` exists so a
developer with no API key can still exercise the whole chain locally.

WHAT THIS FILE DELIBERATELY DOES NOT DO: it never sees a tenant's data. The
callers in main.py send a question and a SCHEMA (model and field names), never
rows. The model's job is to produce a query plan; executing it stays in Odoo,
under that user's own record rules. See the module docstring in main.py.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger("ai_gateway.providers")


class ProviderError(Exception):
    pass


class ModelRefused(Exception):
    """The model declined. Distinct from an error: nothing is broken."""


class AnthropicProvider:
    """Claude, through the official SDK.

    Model default is `claude-opus-5`. Two things about the current API that a
    stale prior gets wrong, and both are 400s rather than warnings:

      * `budget_tokens` is REMOVED. Thinking is configured as
        `{"type": "adaptive"}` and depth comes from `output_config.effort`.
      * Assistant prefill is REMOVED. Response shape is constrained with
        `output_config.format` instead, which is what the NLQ path needs
        anyway -- a schema the API enforces beats a prompt that asks nicely.

    Server-side refusal fallbacks are ON. A policy decline would otherwise just
    stop the request; with `fallbacks="default"` the API re-runs it on a
    fallback model inside the same call. A decline before any output is not
    billed.
    """

    def __init__(self, model: str, effort: str = "medium") -> None:
        try:
            import anthropic  # noqa: PLC0415 - optional per provider
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "provider=anthropic but the `anthropic` package is not installed"
            ) from exc
        self._anthropic = anthropic
        # Zero-arg: resolves ANTHROPIC_API_KEY, then ANTHROPIC_AUTH_TOKEN, then
        # an `ant auth login` profile. Never hardcode a key.
        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort

        # Server-side refusal fallbacks are newer than some pinned SDKs.
        # anthropic 0.75.0 accepts `betas`, `output_config` and `thinking` but
        # NOT `fallbacks` -- passing it raises
        # `TypeError: Messages.create() got an unexpected keyword argument`
        # at request time, which turns every call into a 500. Detected here,
        # once, rather than discovered per request.
        import inspect  # noqa: PLC0415

        self._supports_fallbacks = "fallbacks" in inspect.signature(
            self._client.beta.messages.create
        ).parameters
        if not self._supports_fallbacks:
            logger.warning(
                "anthropic %s does not support server-side refusal fallbacks; a policy "
                "decline will stop the request instead of being retried on a fallback "
                "model. Upgrade the pin in requirements.txt to re-enable.",
                getattr(anthropic, "__version__", "?"),
            )

    def complete(self, system, messages, max_tokens=8000, schema=None, temperature=None):
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }
        if self._supports_fallbacks:
            # `"default"` routes by refusal category, so there is no model list
            # here to go stale. It pairs with the -07-01 header; the older
            # array form pairs with -06-01, and crossing them is a 400.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        if system:
            kwargs["system"] = system
        if schema is not None:
            kwargs["output_config"]["format"] = {
                "type": "json_schema",
                "schema": schema,
            }
        # temperature is REMOVED alongside adaptive thinking on this model
        # family; accepting it here and dropping it keeps the Odoo caller's
        # existing signature working instead of 400-ing on a field it has
        # always sent.
        if temperature is not None:
            logger.debug("ignoring temperature=%s: removed on this model family", temperature)

        try:
            resp = self._client.beta.messages.create(**kwargs)
        except self._anthropic.APIStatusError as exc:
            raise ProviderError("anthropic %s: %s" % (exc.status_code, str(exc)[:300])) from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError("anthropic unreachable: %s" % str(exc)[:200]) from exc
        except TypeError as exc:
            # The SDK raises a bare TypeError when no credential resolves --
            # "Could not resolve authentication method" -- and it is raised at
            # REQUEST time, not at construction. Uncaught it surfaced as a 500,
            # which reads as a bug in this service rather than as an unset key.
            # Measured on a machine with no ANTHROPIC_API_KEY.
            if "authentication" in str(exc).lower():
                raise ProviderError(
                    "no Anthropic credential is configured: set ANTHROPIC_API_KEY, or set "
                    "AI_GATEWAY_PROVIDER=ollama for a local model"
                ) from exc
            raise

        # Always check stop_reason BEFORE reading content: a refusal is an
        # HTTP 200 whose content is not the answer.
        if getattr(resp, "stop_reason", None) == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ModelRefused(getattr(details, "category", None) or "declined")

        text = "".join(b.text for b in resp.content if b.type == "text")
        return {
            "text": text,
            "model": resp.model,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }


class OllamaProvider:
    """A local model, so the chain is runnable without an API key.

    Deliberately thin: no streaming, no tools. It exists to prove the wiring
    end to end on a laptop, not to be the production path.
    """

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        base = base_url.rstrip("/")
        # urlopen honours file://, ftp:// and data:// as well as http(s).
        # OLLAMA_BASE_URL is operator-configured rather than attacker-supplied,
        # but a mistyped or migrated value naming a local path would be read off
        # disk and parsed as a model response. Checked once, at construction, so
        # a bad value fails at startup rather than on someone's first question.
        if not base.startswith(("http://", "https://")):
            raise ProviderError("OLLAMA_BASE_URL must be http:// or https://, got %r" % base_url)
        self._base = base
        self._model = model
        self._timeout = timeout

    def complete(self, system, messages, max_tokens=8000, schema=None, temperature=None):
        payload = {
            "model": self._model,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if schema is not None:
            # Ollama takes a JSON schema in `format`, which is the closest
            # equivalent to output_config.format above.
            payload["format"] = schema
        req = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self._base + "/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                body = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("ollama unreachable: %s" % str(exc)[:200]) from exc
        return {
            "text": (body.get("message") or {}).get("content", ""),
            "model": body.get("model", self._model),
            "usage": {
                "input_tokens": body.get("prompt_eval_count", 0),
                "output_tokens": body.get("eval_count", 0),
            },
        }


def build_provider():
    """Pick a provider from the environment.

    `anthropic` is the default because it is the production path; a deployment
    that wants the local model has to say so, rather than silently degrading to
    it when a key goes missing.
    """
    name = os.environ.get("AI_GATEWAY_PROVIDER", "anthropic").strip().lower()
    if name == "anthropic":
        # `changeme` is this repo's universal placeholder and .env.example is
        # required to carry it (scripts/scan-secrets.py enforces that). The SDK
        # would send it as a real key and collect a 401 that reads like a
        # revoked credential, so it is scrubbed here and the SDK's own
        # no-credential path fires instead -- which complete() turns into a
        # legible "no Anthropic credential is configured".
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key or "changeme" in key:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        return AnthropicProvider(
            model=os.environ.get("AI_GATEWAY_MODEL", "claude-opus-5"),
            effort=os.environ.get("AI_GATEWAY_EFFORT", "medium"),
        )
    if name == "ollama":
        return OllamaProvider(
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
            model=os.environ.get("AI_GATEWAY_MODEL", "llama3.2:3b"),
        )
    raise ProviderError("unknown AI_GATEWAY_PROVIDER %r (anthropic|ollama)" % name)
