---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_ai_bridge
manifest_version: 19.0.0.2.0
---

# custom_ai_bridge

## Purpose
Thin Odoo-side client for the platform's external AI gateway (`ai-gateway` HTTP service which fronts Anthropic Claude, OpenAI, and Ollama). Every Odoo-originated AI call — recommendations on records, chat completions for downstream modules, the BRD analyzer — funnels through this bridge so signing, tenant tagging, timeouts, and on/off toggling are uniform.

Provides one abstract service model (`custom.ai`) and one generic "Ask AI" record-agnostic wizard (`custom.ai.recommend.wizard`). All transport is HMAC-signed via `custom.security.sign_payload` and tagged with `X-Tenant-Id: <env.cr.dbname>`.

## Business Flow
- Downstream code calls `self.env["custom.ai"]._chat(messages, system, model, quality, tools, max_tokens, temperature)` or `._recommend(model, res_id, payload, history, locale)`.
- `_call(path, body)` checks the `custom_ai.enabled` `ir.config_parameter` (raise `UserError` if off), serializes body with `json.dumps(default=str)`, asks `custom.security.sign_payload` for an `X-Custom-Signature: t=<ts>,v1=<hmac>` header, POSTs to `${AI_GATEWAY_URL}/v1/chat` or `/v1/workflow/recommend` via `httpx.Client` with `Timeout(connect=10, read=300, write=30, pool=10)`.
- Non-200 → `UserError(f"AI gateway error {status}: {text[:200]}")`. Network failure → `UserError("AI gateway unreachable: ...")`.
- The "Ask AI" wizard (`custom.ai.recommend.wizard.action_ask`) introspects any record's `_fields`, skips `binary`/`image`, serializes recordsets as `(_name, ids[:5])`, calls `_recommend`, and surfaces `summary` / `next_actions` / `priority` / `tags` / `raw_text` on the wizard form.
- Settings page exposes `custom_ai.enabled`, `custom_ai.quality` (fast/high), `custom_ai.provider_override` (""/anthropic/openai/ollama) — all stored as `ir.config_parameter`.

## Key Models
- `custom.ai` — AbstractModel; the gateway client service. No DB row; methods are `@api.model`.
- `custom.ai.recommend.wizard` — TransientModel; generic "Ask AI about this record" UI invokable from any form view's action menu.
- `res.config.settings` (inherited) — exposes the three `custom_ai.*` config parameters.

## Important Fields
- `custom.ai.recommend.wizard.model_name` (Char, required) — technical model name of the record to ask AI about.
- `custom.ai.recommend.wizard.res_id` (Integer, required) — record id within `model_name`.
- `custom.ai.recommend.wizard.locale` (Char, default `"id_ID"`) — passed to gateway for response language.
- `custom.ai.recommend.wizard.summary` / `next_actions_text` / `priority` / `tags` / `raw_text` (Text/Char, readonly) — populated from gateway response keys `summary`, `next_actions`, `priority`, `tags`, `raw_text`.
- `res.config.settings.custom_ai_enabled` (Boolean, `config_parameter="custom_ai.enabled"`, default True) — master kill switch.
- `res.config.settings.custom_ai_default_quality` (Selection fast/high, `config_parameter="custom_ai.quality"`) — default `quality` tier.
- `res.config.settings.custom_ai_provider_override` (Selection ""/anthropic/openai/ollama, `config_parameter="custom_ai.provider_override"`) — forces a specific provider regardless of gateway default.

## Public Methods
- `custom.ai._enabled()` (`@api.model`) — reads `custom_ai.enabled` param.
- `custom.ai._chat(messages, system=None, model=None, quality="fast", tools=None, max_tokens=4096, temperature=0.7)` — POST `/v1/chat`; always sends `cache_system: True`.
- `custom.ai._recommend(model, res_id, payload, history=None, locale="id_ID")` — POST `/v1/workflow/recommend`.
- `custom.ai._call(path, body)` — internal HMAC+httpx transport; raises `UserError` on any non-200 or `httpx.HTTPError`.
- `custom.ai.recommend.wizard.action_ask()` — best-effort payload extraction + `_recommend` call + redisplay self.

## Integration Points
- **Depends on:** `custom_core` (uses `custom.security.sign_payload`).
- **Inherits from:** `res.config.settings`.
- **Extended by:** `custom_ai_features`, `custom_brd_analyzer`, and any module that wants record-level AI recommendations.
- **External calls:** HTTP POST to `${AI_GATEWAY_URL}` (default `http://ai-gateway:8080`) at paths `/v1/chat` and `/v1/workflow/recommend`. Signed with `GATEWAY_SHARED_SECRET` env var.
- **Cross-vertical:** generic — every vertical can call it.
- **Python deps:** `httpx` (declared in `external_dependencies`).

## Gotchas
- **Gateway URL is env-only**, not an `ir.config_parameter`: read from `AI_GATEWAY_URL` at call-time via `os.environ.get`. Cannot be overridden per-tenant without restarting the worker.
- **`provider_override` setting is stored but never read by the bridge itself.** It's the gateway's job to honor it via a header — currently the bridge does NOT forward it. The setting is effectively a no-op until the gateway is taught to read it (or the bridge is taught to send it).
- **5-minute `read` timeout** is hard-coded to accommodate long Opus 4.7 generations; short-running synchronous calls still pay this ceiling if the gateway hangs.
- **No retry / no circuit breaker** — unlike `custom_adapter_framework`, the bridge raises `UserError` immediately on any failure. Callers must catch if they want resilience.
- **`X-Tenant-Id` is `env.cr.dbname`**, not a slug from `tenant.registry` — works in DB-per-tenant deployments but not in single-DB multi-company setups.
- **`UserError` on `_chat`/`_recommend`** means failures bubble to the user as red dialogs; not suitable for background cron use without an outer try/except.
- **`_recommend` payload contains the wizard's raw record dump** (all non-binary fields, recordsets truncated to first 5 ids) — may include PDP-sensitive data; rely on the gateway to redact or pre-filter at the call site.
- **This module's groups sit on its own `custom_ai_bridge.res_groups_privilege_ai_bridge` privilege** ("AI Bridge") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **AI usage logging / cost tracking** — done elsewhere (`custom_hub_console.custom.hub.ai.usage` rolls up aggregates if the bridge ever exposes `_hub_usage_iter`, which it currently doesn't).
- **Provider routing logic** — bridge is dumb; the `ai-gateway` service decides which provider to call.
- **Streaming responses** — only synchronous request/response.
- **Tool / function calling orchestration** — `tools` is forwarded to the gateway but no agentic loop on the Odoo side.
