---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_whatsapp
manifest_version: 19.0.0.4.0
---

# custom_whatsapp

## Purpose
Canonical WhatsApp messaging channel for the platform. Implements a Meta WhatsApp Cloud API adapter (with a Twilio WhatsApp provider slot) targeted at Indonesian SMB tenants where WhatsApp is the dominant customer-comms channel. Provides per-company account configuration, Meta-approved template management with status polling, PDP-consent-gated outbound queue, async dispatch through `queue_job`, and a public webhook controller for inbound + delivery callbacks.

This is the BRD-canonical landing place for any "send WhatsApp" requirement — vertical modules should integrate via `whatsapp.message.create` + `action_send`, never by re-implementing the Meta HTTP client.

## Business Flow
- An admin creates a `whatsapp.account` (provider = `meta_cloud` or `twilio`) with `phone_number_id`, `business_account_id`, `access_token`, `webhook_verify_token`. `sandbox_mode=True` short-circuits real HTTP calls and returns synthetic message ids.
- Meta is configured to point at `/custom_whatsapp/webhook/<account_id>`. The GET handshake echoes `hub.challenge` if `hub.verify_token` matches the stored token. POST events are 200'd unconditionally (Meta retries aggressively if it sees 5xx).
- Templates are created locally in `draft`, then submitted out-of-band to Meta. The cron `whatsapp.template.cron_poll_template_status` polls `/{waba_id}/message_templates?name=<name>` for `pending_review` rows and updates `status` from Meta's `APPROVED/REJECTED/PENDING/...` enum.
- Outbound flow: a vertical (sale / invoice / helpdesk / POS / cart-abandonment) calls `whatsapp.send.wizard` or directly `whatsapp.message.create(...).action_send()`. Consent gate: marketing-category templates require `pdp.consent.check_consent(partner, "whatsapp_marketing")` — otherwise `UserError`. Utility/authentication categories log-warn but proceed.
- `_do_send` builds the Meta payload (template vs plain text), POSTs through `whatsapp.account._post('messages', payload)`, which applies the shared retry policy (3 attempts, exponential backoff, `Retry-After` on 429) + per-account circuit breaker (10 consecutive failures opens for 1h). Sandbox accounts skip HTTP and stamp `sandbox-<hex>` provider ids.
- On Meta-accepted send: state `draft -> sent`, `provider_message_id = wamid`. Webhook `statuses` entries flip `sent -> delivered -> read` (or `-> failed` with `error_message`). Inbound user messages create a new `whatsapp.message` row with `direction='inbound'`, `state='received'`, partner resolved by last-9-digit phone match.
- Bulk dispatch: `action_send_bulk` dispatches inline for ≤5 records, else `with_delay(channel='root.whatsapp')` enqueues one `queue_job` per recipient.

## Key Models
- `whatsapp.account` — Per-company provider credentials + sandbox flag + circuit breaker state (in-process `_CB_STATE`). Hosts `_request`, `_post`, `_get`, `_get_api_url`, `_get_waba_url`, `_get_headers`, `action_test_connection`.
- `whatsapp.message` — Outbound/inbound queue row; inherits `mail.thread` + `pdp.audited.mixin`. Drives the send + status lifecycle.
- `whatsapp.template` — Local representation of a Meta-approved template, with `body_text` containing `{{n}}` placeholders, stored compute `variables_count`, status synced via cron.
- `whatsapp.send.wizard` — TransientModel used by integration buttons on `sale.order` / `account.move` / `helpdesk.ticket`.
- `sale.order`, `account.move`, `helpdesk.ticket` (inherited) — each adds a "Send WhatsApp" button that opens the wizard.

## Important Fields
- `whatsapp.account.provider` (Selection: meta_cloud/twilio) — drives header + endpoint shape.
- `whatsapp.account.sandbox_mode` (Boolean, default True) — when set, `_do_send` and `_request` short-circuit and synthesize ids; protects accidental quota burn.
- `whatsapp.account.access_token` (Char, `groups='custom_whatsapp.group_manager'`) — plaintext today; manifest description flags migration to `custom.ir.config` encrypted storage as a TODO before prod.
- `whatsapp.account.webhook_verify_token` (Char, group-gated) — shared secret echoed against Meta's `hub.verify_token`.
- `whatsapp.message.state` (Selection: draft/queued/sent/delivered/read/failed/received) — full Meta lifecycle.
- `whatsapp.message.provider_message_id` (Char, indexed) — Meta `wamid`; how the webhook resolves status updates back to local rows.
- `whatsapp.message.consent_verified` (Boolean) — true iff PDP consent check passed pre-send.
- `whatsapp.template.category` (Selection: marketing/utility/authentication) — drives the consent purpose code lookup `_CATEGORY_PURPOSE`.
- `whatsapp.template.status` (Selection: draft/pending_review/approved/rejected, tracking) — only `approved` templates are eligible for template-typed sends; non-approved fall back to plain text.
- `whatsapp.template.variables_count` (Integer, stored compute) — distinct `{{n}}` placeholder positions parsed by `_VAR_RE`.
- `whatsapp.template.meta_template_id` (Char, readonly) — upstream identifier; required for the status cron.

## Public Methods
- `whatsapp.account._request(method, url, json_body=, params=)` — shared HTTP helper with retry + breaker. Raises `RuntimeError` on exhaustion; never echoes the access token in error text.
- `whatsapp.account._post(endpoint, payload)` / `_get(url, params)` — convenience wrappers.
- `whatsapp.account.action_test_connection()` — smoke-test GET on the phone-number resource; returns an Odoo client notification.
- `whatsapp.message.action_send()` — consent-gate + per-record dispatch; never re-raises (records `state='failed'`).
- `whatsapp.message.action_send_bulk()` — dispatches inline for ≤5 records, else through `queue_job` channel `root.whatsapp`.
- `whatsapp.message._build_payload()` — returns the Meta payload dict (template or text).
- `whatsapp.message._apply_status_update(status_payload)` (`@api.model`) — webhook entry point for `statuses` events; maps Meta enum to local `state`.
- `whatsapp.message._record_inbound(account, message_payload, contact_payload)` (`@api.model`) — webhook entry for inbound user messages; resolves partner by last-9-digit phone match.
- `whatsapp.template.cron_poll_template_status()` (`@api.model`) — polls Meta for `pending_review` template approval status.
- Controller routes: `GET /custom_whatsapp/webhook/<account_id>` (verify handshake) + `POST /custom_whatsapp/webhook/<account_id>` (event dispatch), both `auth='public'`, `csrf=False`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_consent`, `custom_pdp_taxonomy`, `mail`, `queue_job`, `sale_management`, `account`, `custom_helpdesk`.
- **Inherits from:** `sale.order`, `account.move`, `helpdesk.ticket` (each gets a Send-WhatsApp button); `mail.thread` + `pdp.audited.mixin` on `whatsapp.message`.
- **Extended by:** `custom_pos_id` (POS e-receipt dispatch), `custom_ecommerce` (cart abandonment reminder via `_can_use_whatsapp`).
- **External calls:** Meta Graph v22.0 (`https://graph.facebook.com/v22.0`): `POST {phone_number_id}/messages`, `GET {phone_number_id}` (probe), `GET {waba_id}/message_templates`.
- **Cross-vertical:** **canonical messaging channel** — every vertical's WhatsApp requirement must map here.
- **queue_job channel:** `root.whatsapp` (declared in `data/queue_job_channel.xml`).

## Gotchas
- **Access token is plaintext Char** today — flagged for migration to `custom.ir.config` encrypted storage. Manager-group-only read, but still on-disk plaintext.
- **Webhook is `auth='public'`** — security relies on the per-account `webhook_verify_token` and the `account_id` URL segment. There is **no HMAC signature check** on POST events despite the manifest mentioning "signature check".
- **Webhook always returns 200** even when dispatch raises (logged via `_logger.exception`) to avoid Meta's aggressive retries. Failed inbound storage will not be re-driven.
- **Per-account circuit breaker is in-process** (`_CB_STATE` dict at module scope) — does not survive worker restarts and is not shared across multiple Odoo workers; each worker counts independently.
- **Sandbox mode is default True** — production rollout requires explicitly flipping the flag per account.
- **Nothing sensitive may go into the sandbox log line.** It used to print the recipient's real number and the first 120 characters of the body; sandbox is normally exercised with live customer data during UAT, and the Odoo log is not a sensitive store. `_do_send` now logs `_mask_phone(to_phone)` (`62********890`), `body_len` and the record id. Keep it that way if the line is ever extended.
- **Marketing consent is hard-gated** (UserError); utility/authentication only log-warn. Make sure vertical modules surface the UserError to the operator.
- **Partner phone matching is last-9-digit `ilike`** — collisions are possible for short numbers; `_record_inbound` picks `limit=1` without disambiguation.
- **`res.partner.mobile` does not exist in Odoo 19.** Reading it raises `AttributeError`; naming it in a domain raises `ValueError` rather than simply not matching, which took every inbound message down. All five sites now go through `res_partner.py`: `partner._whatsapp_phone()` to pick a number to send to, `available_phone_search_fields()` to build a domain. Both filter against `_fields`, so an older build that still has `mobile` keeps preferring it.
- **Inbound matching searches `phone_sanitized` first.** `phone` stores whatever the user typed (`+62 812-3456-7890`), so a last-9-digits `ilike` cannot see past the separators; `phone_sanitized` is the same number in E.164. Do not drop it from `_SEARCH_PHONE_FIELDS` without replacing the normalisation.
- **Template send currently omits `{{n}}` variable substitution** — `_build_payload` for approved templates only sends `name` + `language`, no `components`/`parameters`. Templates with variables will be rejected by Meta.
- **Twilio provider slot is configured but `_get_api_url` only builds Meta Graph URLs** — selecting `twilio` will misroute.

## Out of Scope
- HMAC signature verification of incoming webhook payloads (relies on `account_id` + verify-token only).
- Encrypted at-rest secret storage (TODO before prod per manifest description).
- Media messages (image/document/video) — `_build_payload` handles text + template-without-params only.
- Twilio WhatsApp dispatch (selectable but not implemented).
- Per-user conversation UI / Discuss integration — only the queue + chatter on `whatsapp.message` is shown.
