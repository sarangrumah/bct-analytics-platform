---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_ecommerce
manifest_version: 19.0.0.2.0
---

# custom_ecommerce

## Purpose
Indonesia localization for `website_sale` + `delivery`. Provides a registry of Indonesian couriers (JNE, J&T, SiCepat, AnterAja, Pos Indonesia, Grab, Gojek, Custom), per-carrier service-type/COD metadata, a mock shipping-rate calculator that is RajaOngkir/Komerce-ready (returns a real-adapter-shaped dict so call sites never branch on mock-vs-live), AWB/Resi tracking on `sale.order`, and a cart-abandonment reminder cron that prefers WhatsApp (with PDP consent) and falls back to email.

It does **not** ship its own payment gateway; Midtrans/Xendit checkout entry is delegated to `custom_payment_id`.

## Business Flow
- An admin populates `custom.ecommerce.courier` rows for each provider (`code` from the fixed selection, `api_endpoint`, `api_key` group-gated, `tracking_url_template` with `{awb}` placeholder).
- A standard `delivery.carrier` is linked to a courier via `x_id_courier_id` and tagged with `x_id_service_type` (`REG/YES/OKE/...`). `x_id_cod_supported` + `cod_max_amount` enable COD ceiling validation.
- During checkout, `delivery.carrier.id_rate_shipment(order)` returns the standard delivery-framework dict by wrapping `_get_id_shipping_rate(order)`. The mock rate is `base_rate_per_kg[code] × weight × service_multiplier × distance_factor`, where distance is approximated by comparing first 2 digits of origin/destination ZIP (intra-province = 1.0x, inter = 1.4x). Weight is derived from `order_line.product_id.weight × qty`, minimum 1kg.
- Once shipped, an operator sets `sale.order.x_awb_number`; the stored compute `x_awb_tracking_url` renders the courier's `tracking_url_template` with the AWB number.
- Cart abandonment: cron `cron_send_abandoned_reminders` sweeps draft `sale.order` with `write_date <= now - 24h`, partner not public, at least one line. For each new order it creates a `custom.ecommerce.cart.abandonment` row. If the partner has phone + active `pdp.consent.purpose_marketing` and `custom_whatsapp` is installed, dispatch via WhatsApp; otherwise email via `mail_template_cart_abandonment`. The WhatsApp branch currently posts a chatter marker rather than a real `whatsapp.message` (manifest pattern — actual send delegated to `custom_whatsapp` when integration is wired).
- Indonesian DJP-style invoice receipt qweb report is shipped (referenced in manifest description).

## Key Models
- `custom.ecommerce.courier` — Registry of Indonesian couriers; `code` (jne/jnt/sicepat/anteraja/posindo/grab/gojek/custom), API endpoint, tracking URL template, service types.
- `delivery.carrier` (inherited) — Adds `x_id_courier_id`, `x_id_service_type`, `x_id_cod_supported`, `cod_max_amount`, `currency_id`; `_get_id_shipping_rate`, `id_rate_shipment`.
- `sale.order` (inherited) — Adds `x_awb_number`, stored `x_awb_tracking_url` (computed), and a related read-only `x_id_courier_id` mirror.
- `custom.ecommerce.cart.abandonment` — Abandoned cart record + reminder dispatch state; unique `sale_order_id` constraint.

## Important Fields
- `custom.ecommerce.courier.code` (Selection, required, tracked) — drives the base-rate lookup in `_BASE_RATE_PER_KG`.
- `custom.ecommerce.courier.tracking_url_template` (Char) — `{awb}` placeholder; rendered into `sale.order.x_awb_tracking_url`.
- `delivery.carrier.x_id_courier_id` (M2o `custom.ecommerce.courier`) — link to the localized courier.
- `delivery.carrier.x_id_service_type` (Char) — `REG`/`YES`/`OKE`/`ECO`/`EXP`/`SAMEDAY`/`INSTANT`; multiplier in `_SERVICE_MULTIPLIER`.
- `delivery.carrier.x_id_cod_supported` (Boolean) + `cod_max_amount` (Monetary) — COD ceiling; `_check_cod_max` rejects negative ceilings.
- `sale.order.x_awb_number` (Char) — set by operator post-shipment.
- `sale.order.x_awb_tracking_url` (Char, stored compute) — depends on `x_awb_number`, `carrier_id`, courier's template.
- `custom.ecommerce.cart.abandonment.reminder_channel` (Selection: email/whatsapp/none) — channel actually used by the dispatcher.
- `custom.ecommerce.cart.abandonment.reminder_sent` / `reminder_sent_at` — idempotency markers.

## Public Methods
- `delivery.carrier._get_id_shipping_rate(order)` — mock rate calculator returning `{ok, courier_code, service, weight_kg, origin_zip, destination_zip, cost, currency='IDR', etd_days, raw}`. Override target for RajaOngkir/Komerce live adapters.
- `delivery.carrier.id_rate_shipment(order)` — wraps `_get_id_shipping_rate` into the standard `{success, price, error_message, warning_message, id_rate}` dict.
- `delivery.carrier._check_cod_max()` (`@api.constrains`) — rejects negative COD ceilings.
- `sale.order._compute_awb_url()` — renders the tracking URL.
- `custom.ecommerce.cart.abandonment.cron_send_abandoned_reminders()` (`@api.model`) — cron entry; returns the count of reminders dispatched.
- `custom.ecommerce.cart.abandonment._dispatch_reminder()` / `_can_use_whatsapp(partner)` / `_send_whatsapp_reminder()` / `_send_email_reminder()` — channel selection + dispatch.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `website_sale`, `delivery`, `custom_payment_id`, `mail`.
- **Inherits from:** `sale.order`, `delivery.carrier`; `mail.thread` on registry models.
- **Extended by:** none declared; downstream vertical e-commerce stacks would override `_get_id_shipping_rate` for live courier APIs.
- **External calls:** none today — `api_endpoint` / `api_key` are metadata-only. A future RajaOngkir or Komerce adapter is the intended live path.
- **Cross-vertical:** Indonesia-locked (plate format, courier set, IDR currency, ZIP heuristic).
- **Cart abandonment soft-checks `custom.whatsapp.account`** at runtime (`self.env`), so `custom_whatsapp` is **not** a hard depends.

## Gotchas
- **Shipping rate is mock.** `_get_id_shipping_rate` returns hand-tuned base rates per kg × hand-tuned service multipliers × 2-digit-ZIP distance heuristic. Not for production pricing.
- **WhatsApp abandonment dispatch currently only posts a chatter marker** (`_send_whatsapp_reminder`) — does not actually create a `whatsapp.message`. Sites that need real WA dispatch must override or extend.
- **Cart abandonment soft-imports `custom.whatsapp.account` via `self.env`** — model name mismatch with `whatsapp.account` (the actual model). The current `_can_use_whatsapp` check will always return False because `custom.whatsapp.account` does not exist. Either fix to `whatsapp.account` or align the model name.
- **Currency assumed IDR** — `_get_id_shipping_rate` hardcodes `"currency": "IDR"`; downstream computations may break for multi-currency tenants.
- **COD ceiling enforcement is partial** — the `_check_cod_max` constraint only rejects negatives. Actual validation that order total ≤ `cod_max_amount` is not enforced on `sale.order` itself.
- **Abandonment cutoff is 24h fixed** (`_ABANDONMENT_HOURS=24`) — no config parameter.
- **Distance factor is a 2-digit-ZIP proxy** — adequate for mocks but trivially fooled by city-pair edge cases.
- **`tracking_url_template.format(awb=...)`** catches `KeyError/IndexError/ValueError`; templates with extra unknown placeholders silently produce `False` URL.

## Out of Scope
- Live courier API integration (RajaOngkir / Komerce / direct JNE / J&T APIs).
- Pickup scheduling / pickup-request POST.
- COD reconciliation against carrier settlement.
- Midtrans / Xendit payment flow (delegated to `custom_payment_id`).
- Multi-currency shipping rates.
- AWB auto-generation / printing.
- Real-time tracking polling.
