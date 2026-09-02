---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_payment_id
manifest_version: 19.0.0.1.0
---

# custom_payment_id

## Purpose
Indonesia payment gateway integration on top of Odoo 19's `payment` framework. Registers Midtrans, Xendit, DOKU, and **POS** as additional `payment.provider.code` values (`_ID_CODES = ("midtrans", "xendit", "doku", "pos")`), ships an HTTP adapter base with retry / exponential backoff / per-(db,provider) circuit breaker / outbound call log, and wires four webhook endpoints that verify signatures and transition `payment.transaction` state via documented helpers (`_set_done`/`_set_pending`/`_set_canceled`/`_set_error`).

This is the canonical Indonesia payment-acquirer module. Any BRD requirement involving "Midtrans Snap", "Xendit invoice", "DOKU checkout", "Indonesia payment gateway", or "QRIS / Virtual Account collection" maps here. Cleanly extensible to additional providers by adding `custom.payment.id.adapter.<name>` AbstractModel + provider selection_add.

## Business Flow
- Admin creates a `payment.provider` record with `code` ∈ midtrans/xendit/doku; fills `x_id_server_key` (gated by group_manager), `x_id_client_key`, `x_id_merchant_id` (DOKU), `x_id_sandbox`, `x_id_webhook_secret` (Xendit X-Callback-Token / DOKU HMAC secret).
- `action_test_id_connection()` button → `_get_id_adapter()` returns the concrete adapter model → `adapter.test_connection(provider)` round-trips a minimal POST through `send()`. UI displays HTTP status, latency, log id via `display_notification`.
- **Outbound (create checkout)**: `payment.transaction._send_payment_request` is overridden — for ID-provider transactions, calls `provider._get_id_adapter().create_checkout(provider, tx)` which returns `{redirect_url, reference, raw}`. Stores `x_id_redirect_url`, `provider_reference`, `x_id_raw_response`; calls `tx._set_pending()`.
- `_get_specific_rendering_values(processing_values)` returns `{api_url, redirect_url}` so the storefront redirects the customer to the gateway-hosted page.
- **Inbound (webhook)**: three public endpoints, `csrf=False`, `auth=public`:
  - `/custom_payment_id/webhook/midtrans` — verifies `signature_key == SHA512(order_id+status_code+gross_amount+server_key)` via `MidtransAdapter.verify_notification_signature`. Maps `transaction_status` through `_MIDTRANS_STATE_MAP`. `capture + fraud=challenge` → pending.
  - `/custom_payment_id/webhook/xendit` — verifies `x-callback-token` header against `provider.x_id_webhook_secret` via `XenditAdapter.verify_callback_token`. Maps `status` through `_XENDIT_STATE_MAP`.
  - `/custom_payment_id/webhook/doku` — verifies HMAC `Signature` header (client_id, request_id, timestamp, path, body, secret) via `DokuAdapter.verify_notification_signature`. Maps `transaction.status` through `_DOKU_STATE_MAP`.
- Each webhook calls `_reconcile_transaction(tx, new_state, raw_payload)` which guards already-final states, calls `_set_done/_set_pending/_set_canceled/_set_error`, posts chatter, returns 200/400/404 as appropriate.
- **Refund**: `payment.transaction.action_create_refund(amount=None)` routes ID providers to `adapter.refund(provider, tx, amount=amount)` (subclass-dependent).
- **Outbound call log**: every `IdPaymentAdapter.send()` materialises a `custom.payment.id.log` row (`state` ∈ queued/sent/ok/failed/timeout, `attempt`, `http_status`, `latency_ms`, `request_payload`, `response_payload`, `error_message`).
- **Circuit breaker**: module-level `_CB_STATE` dict keyed by `(db, provider_id)`; `_CB_THRESHOLD=10` consecutive failures opens breaker for `_CB_OPEN_SECONDS=3600`. `_circuit_open` short-circuits `send()` with `UserError`.

## Key Models
- `payment.provider` (inherited) — `selection_add` for midtrans/xendit/doku + 5 config fields (server_key, client_key, merchant_id, sandbox, webhook_secret). Sensitive fields gated by `group_manager`.
- `payment.transaction` (inherited) — `x_id_redirect_url`, `x_id_raw_response`; override `_send_payment_request`, `_get_specific_rendering_values`, `action_create_refund`.
- `payment.token` (inherited) — stub `x_id_saved_token_id` for Midtrans Snap saved-card; no live flow yet.
- `custom.payment.id.adapter.base` (AbstractModel) — HTTP machinery (`send`, retry, breaker, log). Subclass override hooks `_base_url`, `_endpoint`, `_auth_headers`, `create_checkout`, `test_connection`.
- `custom.payment.id.adapter.midtrans` / `custom.payment.id.adapter.xendit` / `custom.payment.id.adapter.doku` / `custom.payment.id.adapter.pos` — concrete AbstractModels (stubs in current revision; log payloads only — live API plumbing deferred per manifest). POS overrides `_base_url` (`sandbox.payment.pos.com` / `payment.pos.com`), `_endpoint` (`/v1/checkout`), `_auth_headers` (`X-POS-Key`/`X-POS-Client`); `create_checkout` returns a placeholder redirect while `x_id_sandbox` is set; webhook signature placeholder is `HMAC-SHA256(webhook_secret, raw_body)` hex in `X-POS-Signature`.
- `custom.payment.id.log` — outbound call audit row. Inherits `mail.thread`, tracking on `state`.
- `IdPaymentWebhookController` — three `http.Controller` routes for inbound notifications.

## Important Fields
- `payment.provider.code` (Selection, extended) — `midtrans`/`xendit`/`doku` added via `selection_add`; `ondelete={"...": "set default"}`.
- `payment.provider.x_id_server_key` (Char, `groups="custom_payment_id.group_manager"`) — Midtrans Server Key / Xendit Secret / DOKU Secret. Required for outbound.
- `payment.provider.x_id_client_key` (Char) — Midtrans Client Key / Xendit Public Key / DOKU Client Id.
- `payment.provider.x_id_merchant_id` (Char) — DOKU merchant id; optional for Midtrans/Xendit.
- `payment.provider.x_id_sandbox` (Boolean, default True) — drives sandbox vs production `_base_url`.
- `payment.provider.x_id_webhook_secret` (Char, `groups="custom_payment_id.group_manager"`) — Xendit callback token / DOKU HMAC secret. Midtrans ignores (uses server_key for signature).
- `payment.transaction.x_id_redirect_url` (Char, readonly) — gateway-hosted checkout URL.
- `payment.transaction.x_id_raw_response` (Text, readonly) — last raw response (capped 65000 chars).
- `payment.transaction.provider_reference` (existing field, populated from adapter response) — gateway-side reference.
- `payment.token.x_id_saved_token_id` (Char) — Midtrans saved-card token.
- `custom.payment.id.log.state` (Selection queued/sent/ok/failed/timeout, tracking, required, indexed).
- `custom.payment.id.log.attempt` (Integer, default 1) — retry counter.
- `custom.payment.id.log.http_status` (Integer) / `latency_ms` (Integer).
- `custom.payment.id.log.request_payload` / `response_payload` (Text) — capped 65000 chars.
- Module-level breaker constants: `_CB_THRESHOLD=10`, `_CB_OPEN_SECONDS=3600`, `_MAX_RETRIES=3`, `_BACKOFF_BASE=1.0`, `_DEFAULT_TIMEOUT=30`.

## Public Methods
- `payment.provider._get_id_adapter()` — returns concrete adapter AbstractModel for ID providers.
- `payment.provider.action_test_id_connection()` — UI button; round-trips through `adapter.test_connection`.
- `payment.transaction._send_payment_request()` (override) — routes ID providers via adapter; sets `_set_pending()`.
- `payment.transaction._get_specific_rendering_values(processing_values)` (override) — returns `{api_url, redirect_url}` for ID providers.
- `payment.transaction.action_create_refund(amount=None)` (override) — routes ID providers via `adapter.refund`.
- `custom.payment.id.adapter.base._get_for_provider(provider)` (`@api.model`) — provider.code → adapter model lookup.
- `custom.payment.id.adapter.base.send(provider, payload, *, transaction=None, method='POST', endpoint_override=None)` (`@api.model`) — public HTTP entry; returns `{ok, http_status, body, latency_ms, log_id}`.
- `custom.payment.id.adapter.base.create_checkout(provider, transaction)` — subclass-implemented; returns `{redirect_url, reference, raw}`.
- `custom.payment.id.adapter.base.test_connection(provider)` — default = ping POST.
- `custom.payment.id.adapter.base._base_url(provider)` / `_endpoint(provider, payload)` / `_auth_headers(provider, body_bytes=None)` — subclass override hooks.
- `IdPaymentWebhookController.midtrans_webhook()` / `xendit_webhook()` / `doku_webhook()` / `pos_webhook()` — `@http.route(csrf=False, auth='public', methods=['POST'])`. POS posts to `/custom_payment_id/webhook/pos` and maps status via `_POS_STATE_MAP` (placeholder shape `{"order_id": "...", "status": "PAID"}`).
- Module helpers: `_circuit_open(env, provider)`, `_circuit_record_success`, `_circuit_record_failure`, `_circuit_reset` (test/ops button).
- `MidtransAdapter.verify_notification_signature(order_id, status_code, gross_amount, server_key, signature_key)`, `XenditAdapter.verify_callback_token(provided, expected)`, `DokuAdapter.verify_notification_signature(client_id, request_id, timestamp, path, body, secret, provided_signature)` — static signature verifiers used by webhooks.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `payment`, `custom_subscription`.
- **Inherits from:** `payment.provider`, `payment.transaction`, `payment.token`. `custom.payment.id.log` inherits `mail.thread`.
- **Extended by:** verticals adding additional Indonesian gateways (e.g. OY!, Brick, Faspay) by subclassing `custom.payment.id.adapter.base` + adding to `payment.provider.code` selection_add.
- **External calls:** HTTPS to Midtrans (`api.midtrans.com` / `api.sandbox.midtrans.com`), Xendit (`api.xendit.co`), DOKU (`api.doku.com`) endpoints; signature verification on inbound webhooks.
- **Cross-vertical:** Indonesia-locked at the provider list; the adapter framework itself is generic.
- **Subscription tie-in:** `custom_subscription` is in depends; payment provider config can be linked to subscription billing flows, though the integration is not auto-wired in this module.

## Gotchas
- **Adapter implementations are STUBS in the current revision** — manifest description states "currently log payloads only — live API plumbing is wired in a follow-up once sandbox credentials are provisioned per tenant". `create_checkout` raises `NotImplementedError` in the base; per-provider subclasses must override.
- **Circuit breaker state is in-process module-level** — does NOT persist across worker restarts and is NOT shared between gunicorn workers. Multi-worker deployments will see inconsistent breaker behaviour.
- **`_set_default` ondelete cascade on `payment.provider.code`** removes the provider when the module is uninstalled — saved providers will be downgraded to the default code, not deleted.
- **Webhook 404 vs 400**: unknown reference returns 404; signature failure returns 400; provider-code mismatch (e.g. Xendit payload hitting Midtrans endpoint) returns 400. Some gateways retry on 5xx but not 4xx — distinguish carefully.
- **Refund needs `hasattr(adapter, "refund")`** — base does NOT declare a `refund` interface; relying on `hasattr` is fragile and will silently raise `UserError("Refund not supported")` even when the method exists but is misnamed.
- **`x_id_raw_response` capped at 65000 chars** — Midtrans bulk responses can exceed this; truncated.
- **`_send_payment_request` defers other-provider transactions via `super(PaymentTransaction, other)._send_payment_request()`** — this works only if the MRO actually has a non-ID provider in `other`; with mixed batches this is correct, but pure-non-ID batches still call `super()` correctly.
- **`_get_specific_rendering_values` returns BOTH `api_url` and `redirect_url`** to the same value — different parts of the framework historically read different keys.
- **DOKU signature verification reads multiple headers** (`Client-Id`, `Request-Id`, `Request-Timestamp`, `Signature`) — webhook path is hardcoded as `/custom_payment_id/webhook/doku`; if the route is reverse-proxied under a prefix the signature will mismatch.
- **Midtrans `signature_key` calculation** is `SHA512(order_id + status_code + gross_amount + server_key)` — `gross_amount` must be the EXACT string Midtrans sent (e.g. `"100000.00"`); any reformat breaks the check.
- **Token model `x_id_saved_token_id`** is a placeholder; no `_create_token` flow wires it up yet.

## Out of Scope
- **Tokenization / saved-card flows** — stubbed but not live (Midtrans Snap saved-card requires their add-on).
- **3-D Secure / OTP UI flows beyond redirect** — Odoo's standard redirect form is sufficient.
- **Payment status polling** — webhook-driven only; no proactive `/charge/{id}/status` poller.
- **Reconciliation against `account.move`** — Odoo's standard `payment.transaction` → invoice linkage handles this; this module does not extend.
- **QRIS / Virtual Account number generation** — provider-side; not modelled separately.
- **Provider-side dispute / chargeback workflows** — `chargeback` status is mapped to `error` but no automated case lifecycle.
