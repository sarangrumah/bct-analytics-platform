---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pos_id
manifest_version: 19.0.0.1.0
---

# custom_pos_id

## Purpose
Indonesia localization for the standard Odoo POS. Adds QRIS (Quick Response Code Indonesian Standard) payment-method metadata with a deterministic EMVCo-TLV payload builder, rupiah rounding for cash kembalian (none / 50 / 100 / 500 / 1000 IDR with up/down/nearest strategies), and electronic-receipt delivery through WhatsApp (`custom_whatsapp`) and SMS (`custom_sms_id`). Includes a simple loyalty-point accrual (1 point per IDR 10,000) wired to `res.partner.x_loyalty_balance`.

This is the canonical POS localization for Indonesian SMB retail; receipt dispatch is integrated with the platform's canonical messaging channels.

## Business Flow
- An admin configures `pos.config` with `x_rupiah_rounding` step + `x_rupiah_rounding_strategy`, optional `x_whatsapp_account_id` and `x_sms_account_id` for e-receipt routing, and toggles `x_eperformance_receipt_whatsapp` / `x_ereceipt_sms`.
- Per `pos.payment.method` an admin sets `x_qris_provider` (`bca`/`bri`/`mandiri`/`dana`/`gopay`/`ovo`/`linkaja`/`shopeepay`/`custom`/`manual`), `x_qris_merchant_id`, `x_qris_merchant_name`, `x_qris_merchant_city`, optional `x_qris_static_qr` binary, and `x_qris_dynamic_supported`.
- Frontend POS or backend calls `pos.payment.method.action_generate_qris_payload(transaction_amount)` -> builds the EMVCo TLV payload (00, 01, 26, 52, 53, 54 amount if >0, 58, 59, 60) + CRC-16/CCITT-FALSE checksum -> renders a PNG QR (via stdlib `qrcode` if available) -> returns `{payload, qr_png_b64, provider}`. Manual provider raises UserError.
- On order finalisation, `pos.order.action_apply_idr_rounding()` computes the rounded change for cash payments via `_idr_round_change_amount`: raw change = amount_paid - amount_total, then `math.ceil/floor/round(raw/step)*step` per the configured strategy. `x_idr_rounding_applied = rounded - raw` is persisted (idempotent).
- Loyalty: `x_loyalty_points_earned` is a stored compute = `floor(amount_total / 10000)`. `action_credit_loyalty()` adds those points to `partner.x_loyalty_balance` (sudo write) and sets `x_loyalty_credited=True` to prevent double-credit.
- E-receipt: `pos.order.action_send_ereceipt()` routes by `x_eperformance_receipt_channel` (`whatsapp` / `sms` / `email` / `print` / `none`). WhatsApp path creates a `whatsapp.message` row using the configured account and `_build_ereceipt_body()` plaintext rendering, then calls `action_send`. SMS path creates a `custom.sms.message` with `purpose='transactional'`. Email/print/none are stub-logged.

## Key Models
- `pos.config` (inherited) — Adds rupiah-rounding config + e-receipt channel toggles + account bindings to `whatsapp.account` and `custom.sms.account`.
- `pos.payment.method` (inherited) — QRIS metadata + payload generator. Builds EMVCo TLV strings via internal `_tlv` and `_crc16_ccitt` helpers.
- `pos.order` (inherited) — IDR rounding fields, loyalty accrual, e-receipt dispatch + tracking.

## Important Fields
- `pos.config.x_rupiah_rounding` (Selection: none/50/100/500/1000, default `100`) — IDR step for cash change.
- `pos.config.x_rupiah_rounding_strategy` (Selection: up/down/nearest, default `nearest`) — favouring merchant/customer/neutral.
- `pos.config.x_whatsapp_account_id` (M2o `whatsapp.account`) — required if dispatching WA receipts.
- `pos.config.x_sms_account_id` (M2o `custom.sms.account`) — required if dispatching SMS receipts.
- `pos.payment.method.x_qris_provider` (Selection: manual/bca/bri/mandiri/dana/gopay/ovo/linkaja/shopeepay/custom, default `manual`) — drives MID stub lookup.
- `pos.payment.method.x_qris_merchant_id` / `x_qris_merchant_name` / `x_qris_merchant_city` — EMVCo fields 26.02, 59, 60.
- `pos.payment.method.x_qris_static_qr` (Binary, attachment) — pre-uploaded static QR image.
- `pos.payment.method.x_qris_dynamic_supported` (Boolean) — drives tag 01 (`12` dynamic vs `11` static).
- `pos.order.x_idr_rounding_applied` (Monetary, readonly) — signed adjustment from raw to rounded change.
- `pos.order.x_idr_rounded_change` (Monetary, unstored compute) — rounded change for display.
- `pos.order.x_loyalty_points_earned` (Integer, stored compute on `amount_total`) — `floor(amount_total / 10000)`.
- `pos.order.x_loyalty_credited` (Boolean, copy=False, readonly) — idempotency marker.
- `pos.order.x_eperformance_receipt_channel` (Selection: whatsapp/sms/email/print/none, tracking) — dispatch router.
- `pos.order.x_eperformance_receipt_sent` (Boolean, tracking) — set after dispatch.

## Public Methods
- `pos.payment.method.action_generate_qris_payload(transaction_amount=0.0)` — returns `{payload, qr_png_b64, provider}`; raises UserError for `manual` provider.
- `pos.payment.method._render_qr_png(payload)` (`@staticmethod`) — base64-PNG via stdlib `qrcode`; returns None if package missing.
- `pos.order.action_apply_idr_rounding()` — persist `x_idr_rounding_applied` (no-op when no cash payment).
- `pos.order._is_cash_payment()` — true if any payment line is `is_cash_count` or journal type `cash`.
- `pos.order._idr_round_change_amount()` — rounded change per config; falls back to raw when no cash or step≤0.
- `pos.order.action_credit_loyalty()` — adds points to `partner.x_loyalty_balance`; idempotent via `x_loyalty_credited`.
- `pos.order._build_ereceipt_body()` — plaintext receipt body (order + lines + total + loyalty).
- `pos.order._send_ereceipt_whatsapp()` / `_send_ereceipt_sms()` — create + send messages via canonical messaging channels.
- `pos.order.action_send_ereceipt()` — channel-routing dispatcher.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `point_of_sale`, `custom_whatsapp`, `custom_sms_id`.
- **Inherits from:** `pos.config`, `pos.payment.method`, `pos.order`.
- **Extended by:** none declared.
- **External calls:** none (QRIS payload is in-house TLV builder; acquirer REST calls are TODO).
- **Cross-vertical:** Indonesia-locked (IDR, QRIS, Pertamina-adjacent SKUs through e-receipt).
- **Coretax / e-Faktur:** **not** integrated here — POS orders are not posted to `coretax.bukti.potong` or e-faktur. That belongs to `custom_coretax` / `custom_coretax_bupot`.

## Gotchas
- **QRIS payload is in-house mocked.** `_PROVIDER_STUB_MIDS` are placeholder strings; a real BCA/BRI/Mandiri integration will return the canonical payload via the acquirer REST API. Treat the generated QR as dev/demo only.
- **MCC 5812 (convenience stores) is hardcoded** in tag 52 — adjust per merchant category if regulator-audited.
- **CRC-16/CCITT-FALSE polynomial 0x1021** is correct for EMVCo but the implementation uses initial 0xFFFF and XORs the byte into the high byte — verify against EMVCo test vectors before relying for production payloads.
- **Loyalty `x_loyalty_balance` is written on `res.partner`** but not declared in this module — assumed to exist (likely from `custom_core` or another extension). If absent, the write will raise.
- **`x_loyalty_points_earned` uses `amount_total`** which includes tax — pure-pre-tax accrual requires a custom override.
- **`_is_cash_payment` uses `getattr(method, "is_cash_count", False)`** — fallback to journal type `cash` is best-effort and may misclassify non-standard journals.
- **WA e-receipt path requires `whatsapp.account` configured on `pos.config`** — surfaces UserError when missing; no graceful fallback to SMS or email.
- **`_send_ereceipt_*` consent gate is delegated** to the messaging channel — SMS is sent with `purpose='transactional'` (consent log-warn only); WhatsApp template category determines marketing vs utility gating (no template attached here means plain-text path which is utility-equivalent).
- **No e-Faktur / Coretax wiring** despite the manifest depending on `custom_pdp_audit` — POS orders do not generate withholding/tax docs.
- **Field name `x_eperformance_receipt_*`** appears to be a typo for `x_e_receipt_*`; preserved for backward compatibility.

## Out of Scope
- Live QRIS acquirer REST integration (BCA Bizz, BRI Mocash, Mandiri Bizz, etc.).
- e-Faktur / Coretax integration (delegated to `custom_coretax*`).
- Inventory / stock-move integration (standard POS handles it).
- Loyalty redemption (only accrual is implemented).
- Multi-currency POS (IDR-only).
- Receipt printing template customisation (uses CE default).
