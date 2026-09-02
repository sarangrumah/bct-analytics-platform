---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_adapter_framework
manifest_version: 19.0.0.2.0
---

# custom_adapter_framework

## Purpose
Generic outbound-integration framework. Any module that talks to an external HTTP API (Coretax, Pajakku, bank H2H, PPOB providers, etc.) registers a `BaseAdapter` subclass via `@register_adapter("name")`, gets a `custom.adapter.config` row in the UI, and inherits: HMAC signing, retry-with-exponential-backoff, closed/open/half-open circuit breaker, and append-only call log. Removes per-adapter reimplementation of HTTP+auth+resilience plumbing.

## Business Flow
- A vendor module subclasses `BaseAdapter` and decorates with `@register_adapter("coretax")` — the class registers into a process-local `_ADAPTER_REGISTRY` dict.
- Ops creates a `custom.adapter.config` record: name, `adapter_type` (selection sourced from registered classes), `base_url`, `auth_method` (none/hmac/bearer/basic), `credential_ref` (key in `ir.config_parameter` holding the secret).
- Business code calls `config.get_adapter()` → returns `cls(config)` instance; then `.call(endpoint, payload, method="POST")` → `AdapterResponse(ok, status_code, data, error, latency_ms, raw_text, headers)`.
- `call()` runs `_cb_precheck` (raise `CircuitBreakerOpenError` if breaker open + cooldown not elapsed); else builds URL+headers, signs body (`X-Timestamp`+`X-Signature` for HMAC auth, `Authorization` for bearer/basic), POSTs via `requests.request(...)`, retries up to `retry_count` times on `RequestException` or 5xx with `min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)` sleep. 4xx is treated as permanent — no retry, no breaker trip.
- On every call success/failure, writes a `custom.adapter.call.log` row (request hash sha256, status, latency, error) and updates `consecutive_failures`. When failures ≥ `circuit_breaker_threshold`, sets `status="circuit_open"` and stamps `circuit_opened_at`. After `circuit_breaker_cooldown_s` elapsed, next call probes (half-open); success → closed, failure → re-opened.
- `action_health_check()` button calls `cls.health_check()` (default: GET `/health`) and stores `last_health_check`/`last_health_ok`.
- `action_reset_circuit()`, `action_disable()`, `action_enable()` are manual ops toggles.

## Key Models
- `custom.adapter.config` — Per-tenant per-adapter configuration record. Inherits `pdp.audited.mixin`, `mail.thread`. Holds base_url, auth, secret pointer, timeouts, breaker state.
- `custom.adapter.call.log` — Append-only call log; `write()` raises, `unlink()` only as superuser. SHA-256 hash of request body, status, latency, error.
- `BaseAdapter` (plain Python, not an Odoo model) — Subclass-this base providing `call()`, `health_check()`, HMAC signing, retry loop, circuit breaker.

## Important Fields
- `custom.adapter.config.name` (Char, unique, indexed) — adapter instance identifier (e.g. `coretax_prod`, `pajakku_uat`).
- `custom.adapter.config.adapter_type` (Selection, dynamic via `_selection_adapter_type` → registered classes) — picks the Python implementation.
- `custom.adapter.config.base_url` (Char, required) — service root; endpoint paths are appended.
- `custom.adapter.config.auth_method` (Selection none/hmac/bearer/basic, default `hmac`) — drives `_build_headers`.
- `custom.adapter.config.credential_ref` (Char) — KEY in `ir.config_parameter` holding the secret (NOT the secret itself; usually `ENC::...` via `custom.ir.config`).
- `custom.adapter.config.timeout_s` / `retry_count` / `circuit_breaker_threshold` / `circuit_breaker_cooldown_s` (Integer) — resilience knobs. Defaults 15/3/5/60.
- `custom.adapter.config.consecutive_failures` (Integer, readonly) — breaker counter; resets on success.
- `custom.adapter.config.status` (Selection active/disabled/circuit_open, indexed) — current state.
- `custom.adapter.config.circuit_opened_at` (Datetime, readonly) — used to compute cooldown elapsed.
- `custom.adapter.config.last_health_check` / `last_health_ok` — stamped by `action_health_check`.
- `custom.adapter.call.log.config_id` (M2o, ondelete restrict, indexed) — back-link.
- `custom.adapter.call.log.request_hash` (Char, indexed) — `sha256(body)` hex; body bytes never stored to keep log small + PDP-safe.
- `custom.adapter.call.log.response_status` / `latency_ms` / `ok` / `error` — outcome metrics.
- `custom.adapter.call.log.called_at` (Datetime, indexed) — append timestamp.

## Public Methods
- `custom.adapter.config.get_adapter()` — instantiate registered class; raises `UserError` if disabled or type unknown.
- `custom.adapter.config.action_health_check()` — runs `health_check()` per record.
- `custom.adapter.config.action_reset_circuit()` / `action_disable()` / `action_enable()` — ops buttons.
- `BaseAdapter.call(endpoint, payload, timeout=None, method="POST", extra_headers=None)` — main entry, returns `AdapterResponse`.
- `BaseAdapter.health_check()` — default GET `/health`.
- `BaseAdapter._sign_request(body, ts)` — HMAC-SHA256(secret, `ts.encode() + body`), hex.
- `register_adapter(name)` — decorator (module-level) to register a subclass.
- `get_adapter_class(name)` / `list_adapter_classes()` / `unregister_adapter(name)` — registry helpers.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`.
- **Inherits from:** `pdp.audited.mixin`, `mail.thread` (on `custom.adapter.config`).
- **Extended by:** every Coretax/Pajakku/PPOB/H2H/etc. adapter module — they subclass `BaseAdapter` and add their own `custom.adapter.config` rows via data XML.
- **External calls:** the framework itself makes none; subclasses do. Transport is `requests.request(...)`.
- **Cross-vertical:** generic.

## Gotchas
- **`_ADAPTER_REGISTRY` is process-local Python state.** Adapter classes must be imported at module load (typically via `models/__init__.py`) or `adapter_type` selection will be empty and `get_adapter_class` returns None.
- **Circuit breaker state is stored on the config row**, not the adapter instance — across workers the DB row is the only shared state. The half-open probe transition is a quick `sudo().write({"status": "active"})` in-memory only; concurrent workers may probe simultaneously.
- **4xx is treated as permanent failure**: not retried, does not trip breaker. If your upstream returns 429 with intent for retry, the framework will NOT honor it — wrap in custom logic.
- **`credential_ref` is a KEY name** in `ir.config_parameter`. The framework reads it with `sudo().get_param(...)`. To use Fernet-encrypted values, store via `custom.ir.config.set_encrypted` so the value carries the `ENC::` prefix — but the framework itself calls `get_param`, not `get_encrypted`, so encrypted values come back raw. **This is a known gap** — adapters needing encrypted creds must decrypt inside their subclass.
- **`call.log.write()` raises UserError** — even superuser cannot edit a log row. Only `unlink()` is permitted, and only for superuser.
- **No tenant_id on call.log** — relies on DB-per-tenant isolation.
- **`AdapterResponse.error` for non-ok responses is either the JSON `data.error` or `f"HTTP{status_code}"`** — no structured error model.
- **`time.sleep` in the retry loop blocks the worker** — long retries on slow upstream will tie up a worker thread.
- **This module's groups sit on its own `custom_adapter_framework.res_groups_privilege_adapter_framework` privilege** ("Adapter Framework") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Inbound webhooks** — this module is outbound only. Inbound is handled by `custom_core.controllers.secure_endpoint`.
- **OAuth / mTLS** — only none/hmac/bearer/basic supported.
- **Per-call audit beyond hash + status** — the request body itself is not stored (sha256 only). Vendor modules wanting full audit must layer on top.
- **Async / batch** — synchronous `requests` only.
