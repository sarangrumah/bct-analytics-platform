---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_coretax_pajakku
manifest_version: 19.0.0.1.0
---

# custom_coretax_pajakku

## Purpose
**Canonical concrete Pajakku ASPP (Authorized Service Provider Pajak) adapter** for the platform's Coretax stack. This module IS the host-to-host bridge between `custom_coretax`'s XML generators and the live Pajakku (mitrapajakku) REST API: OAuth2 client-credentials token cache, exponential-backoff retry, HTTP 429 Retry-After handling, circuit breaker, transaction ledger, per-tenant per-month usage meter, and a 30-minute sync cron that polls submission status and stamps approved NSFP back onto the source `account.move` / `custom.coretax.bukti.potong`.

This is the locked Phase-2 ASPP choice — verticals already subscribe to Pajakku. Other ASPPs (OnlinePajak, Klikpajak, Pajak.io) are intentionally separate sibling adapters that share the same `custom.coretax.adapter.base` contract.

## Business Flow
- **Per-tenant setup**: admin opens **Coretax Config**, switches `adapter_type` to `pajakku`, ticks `pajakku_enabled`, leaves `pajakku_sandbox_mode=True`, fills `pajakku_client_id`, then uses **Set / Rotate Secret…** wizard (`custom.coretax.pajakku.secret.wizard`) to store the client secret encrypted at rest via `custom.ir.config.set_encrypted` (Fernet wrap with master KMS key). **Test Connection** runs a real OAuth2 exchange and stamps `pajakku_last_test` + `pajakku_last_test_ok` + `pajakku_last_test_message`.
- **Dispatcher hook**: the `custom.coretax.adapter.base._get_for_config` is overridden so that whenever `config.adapter_type == "pajakku"`, the resolver returns `custom.coretax.adapter.pajakku`.
- **Submit**: `custom_coretax` calls `adapter.submit_xml(xml_bytes, config=…, transaction_type=…, source_record=…)`. The adapter creates a `custom.coretax.transaction` row in `submitting` state, performs `POST /api/v1/{efaktur|bupot|coretax}/submit` with Bearer token + multipart XML, parses `submission_uuid`, marks `submitted`, bumps usage `faktur_submits` or `bupot_submits`, returns `{submission_uuid, status, message, transaction_id}`.
- **Resiliency**: `_http_request` retries up to `_MAX_RETRIES=3` attempts with backoff `1s → 2s → 4s`. HTTP 401 → force token refresh + retry once. HTTP 429 → sleep `min(Retry-After, 30s)` + retry. HTTP 5xx + transport errors → backoff retry. Each call bumps `api_calls`.
- **Circuit breaker** (module-globals `_CB_STATE`): `_CB_THRESHOLD=10` consecutive failures opens the breaker for `_CB_OPEN_SECONDS=3600` (1 hour). When tripped, posts a `mail.mt_note` on the config's chatter and `submit_xml` raises `UserError` immediately. Auto-reset after window.
- **Token cache** (module-global `_TOKEN_CACHE` keyed by `cr.dbname`): in-process dict with `{token, expires_at}`; `_get_token` returns cached unless within 30s of expiry or `force_refresh=True`. OAuth2 endpoint `/oauth/token` with `grant_type=client_credentials` + scope `efaktur:write bupot:write`.
- **Poll cron** `_cron_poll_pending` (every 30 min): for each `submitted` transaction, calls `query_nsfp(uuid)`; on `approved` → `mark_approved(nsfp)` writes NSFP back to `account.move.x_custom_nsfp` + `x_custom_coretax_status='approved'` (or `bukti_potong.no_bupot`); on `rejected` → `mark_rejected(code, message)` with chatter post + status flip. Also retries `queued` transactions with `retry_count < _MAX_RETRIES`.
- **Audit**: every state transition on `custom.coretax.transaction` writes `pdp.audit_log` via `pdp.audited.mixin` with classification `financial` (actions `coretax_pajakku_submitted` / `_approved` / `_rejected` / `_error`).
- **Usage metering**: `custom.coretax.pajakku.usage.increment(kind, company=…)` atomic SQL `UPDATE` per company per month (`unique(company_id, period)` constraint).

## Key Models
- `custom.coretax.adapter.pajakku` (AbstractModel) — Concrete adapter implementing `submit_xml` / `query_nsfp` / `download_response` / `test_connection`.
- `custom.coretax.adapter.base` (extended) — Dispatcher override registering `pajakku` adapter.
- `custom.coretax.transaction` — Per-submission ledger row (queued/submitting/submitted/approved/rejected/error).
- `custom.coretax.pajakku.usage` — Per-company-per-month counters (api_calls / faktur_submits / bupot_submits / errors).
- `custom.coretax.config` (extended) — Adds Pajakku fields, secret-set flag, test-connection action.
- `custom.coretax.pajakku.secret.wizard` (TransientModel) — Capture + encrypt client secret.

## Important Fields
- `custom.coretax.config.adapter_type` (extended Selection adding `pajakku`) — dispatcher key.
- `custom.coretax.config.pajakku_enabled` (Boolean) — master kill-switch; even with credentials set, adapter refuses to send while False.
- `custom.coretax.config.pajakku_api_url` (Char) — override; defaults to `https://sandbox-api.pajakku.com` or `https://api.pajakku.com`.
- `custom.coretax.config.pajakku_sandbox_mode` (Boolean, default True).
- `custom.coretax.config.pajakku_client_id` (Char).
- `custom.coretax.config.pajakku_client_secret_set` (Boolean, computed) — presence indicator; actual ciphertext lives in `custom.ir.config` keyed `custom_coretax_pajakku.client_secret.{config.id}`.
- `custom.coretax.config.pajakku_last_test` / `pajakku_last_test_ok` / `pajakku_last_test_message` — Test Connection result.
- `custom.coretax.config.pajakku_pending_tx` / `pajakku_error_tx` (Integer, computed) — dashboard counters.
- `custom.coretax.transaction.transaction_type` (Selection: efaktur_keluaran/masukan, bupot_pph21/23/26/4(2)/unifikasi).
- `custom.coretax.transaction.state` (queued/submitting/submitted/approved/rejected/error).
- `custom.coretax.transaction.external_uuid` (Char, indexed) — Pajakku submission UUID.
- `custom.coretax.transaction.nsfp` (Char, tracking) — DJP-issued number; written back to source doc on approval.
- `custom.coretax.transaction.payload` / `response_xml` / `response_pdf` (Binary, attachment) — audit artifacts.
- `custom.coretax.transaction.retry_count` (Integer, readonly).
- `custom.coretax.pajakku.usage.period` (Date, required) — first day of month; `unique(company_id, period)` constraint.

## Public Methods
- `custom.coretax.adapter.pajakku.submit_xml(xml_bytes, *, config, transaction_type, source_record)` — Materialise transaction + POST + return dict; raises UserError on circuit-breaker open or terminal error.
- `custom.coretax.adapter.pajakku.query_nsfp(submission_uuid, *, config)` — GET status; flips transaction state on approved/rejected.
- `custom.coretax.adapter.pajakku.download_response(submission_uuid, *, config)` — GET response bytes.
- `custom.coretax.adapter.pajakku.test_connection(config)` — Force-refresh OAuth2 token; returns `{ok, message, sandbox}`.
- `custom.coretax.adapter.pajakku._cron_poll_pending()` (`@api.model`) — 30-min cron entry.
- `custom.coretax.config.action_pajakku_set_secret()` — Open secret-capture wizard.
- `custom.coretax.config.action_pajakku_test_connection()` — Run Test Connection + display notification.
- `custom.coretax.config._pajakku_get_client_secret()` — Decrypt secret from `custom.ir.config`.
- `custom.coretax.transaction.mark_submitting() / mark_submitted(uuid, response_xml=None) / mark_approved(nsfp, response_pdf=None) / mark_rejected(code, message) / mark_error(error, increment_retry=True)` — State helpers (each writes `pdp.audit_log`).
- `custom.coretax.transaction.action_retry()` — Re-queue an errored/rejected transaction.
- `custom.coretax.pajakku.usage.increment(kind, company=None, by=1)` (`@api.model`) — Atomic SQL `UPDATE` counter bump.
- `custom.coretax.pajakku.secret.wizard.action_save()` — Encrypt + store secret via `custom.ir.config.set_encrypted`.

## Integration Points
- **Depends on:** `custom_core` (for `custom.ir.config` Fernet helpers), `custom_pdp_taxonomy`, `custom_pdp_audit` (audit chain), `custom_coretax` (base adapter contract + config model + bukti potong model). External Python: `requests`.
- **Inherits from:** `custom.coretax.adapter.base` (registers + implements), `custom.coretax.config` (Pajakku fields + actions), `mail.thread` + `pdp.audited.mixin` (transaction).
- **Extended by:** None directly. Sibling adapter modules (OnlinePajak, Klikpajak, Pajak.io) would inherit the same `custom.coretax.adapter.base` independently and add themselves to `adapter_type`.
- **External calls:** **Real Pajakku REST API** — `POST /oauth/token`, `POST /api/v1/efaktur/submit`, `POST /api/v1/bupot/submit`, `GET /api/v1/efaktur/{uuid}/status`, `GET /api/v1/efaktur/{uuid}/response`. Sandbox at `https://sandbox-api.pajakku.com`, production at `https://api.pajakku.com`.
- **Cross-vertical:** All Indonesian verticals doing e-Faktur / Bupot submission consume this via `custom_coretax` — the platform-locked Phase-2 ASPP.

## Gotchas
- **CRITICAL: This is `custom_coretax_pajakku`, NOT `custom_coretax`.** `custom_coretax` is the upstream module that defines the **abstract** adapter contract (`custom.coretax.adapter.base`), the XML generators, the `custom.coretax.config` model, and the `custom.coretax.bukti.potong` model. **This module** (`custom_coretax_pajakku`) is the **concrete Pajakku ASPP implementation** of that contract. BRD analyzers and casual readers frequently conflate the two — use only `custom_coretax_pajakku` when discussing Pajakku ASPP integration, OAuth2, retries, circuit breaker, transaction ledger, usage metering, or NSFP polling cron.
- **No bundled mock server** — Phase-2 locked decision (see README). Without valid sandbox credentials, `submit_xml` raises `UserError`. The adapter code is production-grade and works unchanged against the real Pajakku API once credentials + Test Connection succeed.
- **Module-global state**: `_TOKEN_CACHE` (keyed by `cr.dbname`) and `_CB_STATE` (keyed by `company_id`) are **process-local Python dicts**. They are NOT shared across workers / containers — each Odoo worker has its own breaker state. This is by design (acceptable for the platform's scale) but means a worker that just opened the breaker won't block submits from another worker until that worker also accumulates 10 consecutive failures.
- **OAuth2 scope is hardcoded** `"efaktur:write bupot:write"` — adding new endpoints (e.g. PPh 22) requires scope expansion in code.
- **`mark_approved` writes to `account.move.x_custom_nsfp`** but `mark_rejected` writes to `account.move.coretax_status` (no `x_` prefix) — naming inconsistency reflects `custom_coretax`'s mixed field naming; check the live model.
- **`_resolve_config` falls back to `env.company`** when called without config; multi-company tenants must always pass `config=` explicitly to avoid cross-company submissions.
- **Token cache expiry guard is `expires_at > now + 30`** — refreshes 30s before actual expiry to avoid races.
- **`_cron_poll_pending` doesn't honour the circuit breaker for `query_nsfp`** — only `submit_xml` checks `_circuit_open`. Polling will keep hitting the API even when the breaker is open.
- **`download_response` is implemented** but no caller in the current module — it exists for the base contract.
- **Usage counter uses raw `cr.execute` SQL** for atomic increment — bypasses ORM `write`, no `pdp.audit_log` row for usage bumps.
- **Sandbox toggle interacts with `pajakku_api_url`** — explicit URL overrides the sandbox/prod selector.

## Out of Scope
- Bundled mock Pajakku server (deliberate Phase-2 decision).
- Webhook ingestion / push notifications from Pajakku — only 30-min polling.
- Other ASPPs (OnlinePajak, Klikpajak, Pajak.io) — separate sibling adapter modules.
- Manual XML upload fallback — that workflow lives in `custom_coretax`.
- SLA dashboard powered by usage metering — listed as roadmap.
- Bukti Potong PPh 22 (not in `TRANSACTION_TYPES` selection).
- Cross-tenant token sharing (cache is process-local).
