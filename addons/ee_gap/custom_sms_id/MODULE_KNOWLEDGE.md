---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_sms_id
manifest_version: 19.0.0.1.0
---

# custom_sms_id

## Purpose
Canonical SMS messaging channel for the platform. Multi-provider SMS adapter for Indonesian SMB tenants supporting Zenziva (Indonesia local) and Twilio (global), with a pluggable adapter pattern (`custom.sms.adapter.base` + per-provider AbstractModel subclasses), PDP-consent gating on marketing sends, and bridging through the standard Odoo `sms.sms` queue.

This is the BRD-canonical landing place for any "send SMS" requirement — OTP, transactional notifications, and (consented) marketing campaigns. Vertical modules should create a `custom.sms.message` and call `action_send`, not implement a provider HTTP client.

## Business Flow
- An admin creates a `custom.sms.account` choosing `provider` (`zenziva` / `twilio`) and supplying provider-shaped credentials (Zenziva: `userkey` + `passkey`; Twilio: `account_sid` + `auth_token`). `sandbox_mode=True` by default short-circuits real HTTP.
- A vertical creates a `custom.sms.message(account_id, to_phone, body, purpose)` where `purpose ∈ {otp, transactional, marketing}`.
- `action_send()` resolves consent: looks up the purpose-mapped code in `_PURPOSE_CONSENT_CODE` (`marketing -> sms_marketing`, `transactional`/`otp -> sms_transactional`) and calls `pdp.consent.check_consent(partner, code)`. If `purpose == 'marketing'` and consent missing -> `UserError`. Other purposes log-warn and proceed.
- Adapter dispatch: `custom.sms.adapter.base._get_for_account(account)` resolves to `custom.sms.adapter.zenziva` or `custom.sms.adapter.twilio`. The adapter's `send(account, to_phone, body, purpose=)` returns `{ok, provider_message_id, message}`.
- HTTP layer (`adapter_base._post`): 3 retries, exponential backoff (1/2/4s), `Retry-After` honoured on 429, per-account circuit breaker (10 failures within 60s -> open for 5min). Sandbox skips HTTP entirely.
- On success: `state = 'sent'`, `provider_message_id` stamped, `sent_at = now`. On failure: `state = 'failed'`, `error_message` set, never re-raises.
- Bridge: `sms.sms._send` is overridden — when an active `custom.sms.account` exists for the current company, the SMS is routed through the custom adapter instead of Odoo IAP; otherwise it falls back to upstream IAP send. Sent rows store `x_custom_account_id` for traceability.

## Key Models
- `custom.sms.account` — Per-company per-provider configuration; `sender_id`, `sandbox_mode`, credentials. `action_test_connection` probes via the resolved adapter.
- `custom.sms.message` — Outbound queue row; inherits `mail.thread` + `pdp.audited.mixin`.
- `custom.sms.adapter.base` (AbstractModel) — Dispatcher (`_get_for_account`) + shared HTTP helper `_post` with retry/breaker. Defines `send`/`test_connection`/`poll_status` abstract API.
- `custom.sms.adapter.zenziva` (AbstractModel, inherits base) — Real form-encoded POST to `https://console.zenziva.net/reguler/api/sendsms/`; parses `status=1`/`messageid` JSON response (handles `data`/`messagedata` variants).
- `custom.sms.adapter.twilio` (AbstractModel, inherits base) — Twilio provider slot.
- `sms.sms` (inherited) — `_send` override; adds `x_custom_account_id` traceability field.

## Important Fields
- `custom.sms.account.provider` (Selection: zenziva/twilio) — drives adapter resolution.
- `custom.sms.account.sandbox_mode` (Boolean, default True) — skip real HTTP; return synthetic `zenziva_sandbox_<hex>` ids.
- `custom.sms.account.userkey` / `passkey` (Char, passkey group-gated `custom_sms_id.group_manager`) — Zenziva credentials.
- `custom.sms.account.account_sid` / `auth_token` (Char, auth_token group-gated) — Twilio credentials.
- `custom.sms.account.sender_id` (Char, default `CUSTOM`) — alphanumeric sender ID / shortcode.
- `custom.sms.message.purpose` (Selection: otp/transactional/marketing) — drives consent gating severity (hard-gate marketing only).
- `custom.sms.message.state` (Selection: draft/queued/sent/delivered/failed, tracking).
- `custom.sms.message.consent_verified` (Boolean, readonly) — true iff PDP consent check passed pre-send.
- `custom.sms.message.provider_message_id` (Char, readonly) — upstream id returned on accept.
- `sms.sms.x_custom_account_id` (M2o `custom.sms.account`, readonly) — set automatically when routed through the custom adapter.

## Public Methods
- `custom.sms.adapter.base._get_for_account(account)` (`@api.model`) — adapter resolver; raises UserError for unknown provider.
- `custom.sms.adapter.base.send(account, to_phone, body, *, purpose=None)` (`@api.model`, abstract) — `{ok, provider_message_id, message}`.
- `custom.sms.adapter.base.test_connection(account)` (`@api.model`) — credential probe.
- `custom.sms.adapter.base.poll_status(account, provider_message_id)` (`@api.model`) — optional DLR lookup; Zenziva returns `ok=False` (webhook-only).
- `custom.sms.adapter.base._post(url, data, *, auth=, timeout=30, account=)` — shared HTTP helper with retry + breaker.
- `custom.sms.adapter.base._check_circuit(account)` — raises UserError if breaker open.
- `custom.sms.message.action_send()` — consent-gate then dispatch via resolved adapter.
- `custom.sms.account.action_test_connection()` — calls the adapter `test_connection` and shows a notification.
- `sms.sms._send(unlink_failed=, unlink_sent=, raise_exception=)` — overridden router; splits recordset into custom-adapter vs IAP fallback.
- `sms.sms._resolve_custom_account()` — picks the active company-scoped or shared account.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_consent`, `mail`, `sms`.
- **Inherits from:** `sms.sms` (`_send` override); `mail.thread` + `pdp.audited.mixin` on `custom.sms.message`.
- **Extended by:** `custom_pos_id` (POS e-receipt SMS), `custom_ecommerce` (cart-abandonment fallback). Likely many vertical OTP flows.
- **External calls:** Zenziva regular API `https://console.zenziva.net/reguler/api/sendsms/` (form-encoded POST); Twilio (slot only).
- **Cross-vertical:** **canonical SMS channel** — every vertical's SMS requirement must map here.

## Gotchas
- **Twilio adapter is a slot** — present in dispatcher but the concrete file is `adapter_twilio.py`; verify behaviour before going live (this knowledge base did not deep-inspect it).
- **`passkey` / `auth_token` stored as plain Char** today, manager-group-gated. Manifest description flags migration to encrypted storage before prod.
- **Circuit breaker is in-process `_CB_STATE` dict** — per worker, not shared, lost on restart. Multi-worker environments may temporarily exceed thresholds before each worker trips.
- **Zenziva regular API does not expose status lookup** — DLR delivery reports must arrive via webhook (not implemented here). `state` will stay at `sent` even after delivery.
- **`sms.sms._send` override silently routes through custom adapter** when an account is active — operators may be surprised that Odoo IAP credentials are bypassed. Inspect `sms.sms.x_custom_account_id` to trace which records used the custom path.
- **Sent records are unlinked when `unlink_sent=True`** (default) — chatter history on the original sender object is the only audit trail; `custom.sms.message` is the durable record.
- **Marketing consent is hard-gated, OTP/transactional are not** — make sure callers tag `purpose` correctly; defaulting to `transactional` silences consent failures.
- **Adapter resolver raises UserError for unknown provider** — adding a third provider requires extending `_get_for_account`.

## Out of Scope
- DLR webhook ingestion (delivery / failure callbacks).
- Async `queue_job` dispatch (sends are inline; for bulk use `custom_whatsapp` or wrap manually).
- Encrypted at-rest credentials (TODO).
- Inbound SMS / two-way conversation.
- Templated body rendering with placeholder substitution.
- Cost / quota tracking per account.
