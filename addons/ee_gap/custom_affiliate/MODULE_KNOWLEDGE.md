# custom_affiliate

Affiliate program for the platform (Odoo CE has none). Spec: §6 of
`docs/projects/gentlewoman/spec-headless-fashion-commerce-ai.md`. First consumer: the Gentle Woman
headless storefront, but the module is channel-agnostic (attribution fires on
any `sale.order.action_confirm`).

## Models
- `custom.affiliate` — master: `partner_id`, unique `affiliate_code` (seq `AFF%05d`),
  `state` (draft/active/suspended), `commission_rate` %, payout method. Helper
  `_resolve_active(code)` (case-insensitive, active only). `mail.thread`.
- `custom.affiliate.link` — tracked links: `short_code` (`secrets.token_urlsafe`),
  `target_url`, UTM, computed `full_url` = `storefront_base_url` + path + `?aff=CODE`.
- `custom.affiliate.click` — analytics: hashed IP/UA (sha256[:32], **no raw PII**),
  session, landing, referrer. `_record_click(...)` de-dups per (affiliate, session)
  within `dedup_seconds` (default 60).
- `custom.affiliate.conversion` — one per order (`unique(sale_order_id)`):
  `order_value`, `commission_rate`, `commission_amount`, state
  pending→approved→paid (or reversed). `_cron_affiliate_maintenance` reverses
  cancelled-order conversions and approves pending ones older than the reversal
  window. `mail.thread`.
- `custom.affiliate.payout` — settlement batch (seq `AFP/YYYY/####`):
  `action_collect_approved` pulls the affiliate's approved+unpaid conversions;
  `action_mark_paid` flips them + itself to paid.
- `sale.order` — `custom_affiliate_code` (captured at checkout) + `custom_affiliate_id`.
  `action_confirm` → `_affiliate_create_conversion()`.

## Attribution (spec §8) — all `ir.config_parameter`, defaults seeded
`custom_affiliate.{attribution_model=last_click, attribution_window_days=30,
reversal_window_days=14, default_commission_rate=10, storefront_base_url, cors_origin}`.
Commission base = **`amount_untaxed`** (goods value, excl tax/shipping).
The 30-day last-click window is enforced **client-side** via the `aff_ref` cookie
TTL; the server trusts the code present on the order. Conversion lifecycle window
(`reversal_window_days`) is enforced by the daily cron.

## Anti-fraud
Self-referral blocked (`affiliate.partner_id.commercial_partner_id ==
order.partner_id.commercial_partner_id`); unknown/inactive code ignored silently;
click de-dup per (affiliate, session); IP/UA hashed.

## Tracking flow (headless)
1. Visitor lands on storefront `?aff=CODE`.
2. Next.js `AffiliateCapture` (lib/affiliate.ts) sets first-party `aff_ref` cookie
   (consent-gated — skipped if `gw-cookie-consent == rejected`), TTL 30d, and pings
   `GET /api/affiliate/track` → BFF (`app/api/affiliate/track`) → Odoo public
   `GET /affiliate/track` (CORS) which validates the code + records the click.
3. At checkout the storefront sends `affiliate_code` (from `aff_ref`); custom_storefront_api
   `/checkout` writes it to the order **only if the field exists** (`'custom_affiliate_code'
   in cart._fields`) — soft dependency, no import of this module.
4. `action_confirm` → pending conversion + commission.

## Deploy / scope
gentlewoman only: `odoo -i custom_affiliate -d gentlewoman` then restart the live
Odoo container (new Python: controller routes + sale.order override). Other tenants
don't have it. Set `custom_affiliate.storefront_base_url` per tenant after install.

## Self-serve dashboard + share-to-social (DONE)
Customer-facing JWT endpoints live in **custom_storefront_api** (`customer_api.py`,
guarded by `'custom.affiliate' in request.env`): `GET /storefront/api/affiliate/me`
(→ `custom.affiliate._storefront_dashboard()` = code/rate/stats/links, or
`{is_affiliate:false}`), `POST /affiliate/apply` (creates an **active** affiliate for the
current partner — self-serve auto-activate; admin can suspend), `POST /affiliate/links`
({name, target_url} → `custom.affiliate.link`, returns dashboard). Frontend page
`/account/affiliate`: apply CTA → dashboard (clicks/conversions/earned/pending stats,
link generator, per-link share row → WhatsApp/Facebook/X/Telegram/Copy intent URLs).
Serialization helpers (`_storefront_dashboard`, link `_storefront_serialize`) live in
THIS module; the storefront only routes JWT → calls them.

## OG preview cards (DONE — storefront side)
Shared product links render rich cards: the storefront PDP was split into a server
`page.tsx` (exports `generateMetadata` → OG/Twitter tags via `getProductServer` in
`lib/odoo.ts`, absolute `og:image` = `${NEXT_PUBLIC_SITE_URL}/api/img/web/image/
product.template/<id>/image_1024`) + `ProductDetailClient.tsx` for interactivity.
Affiliate `?aff=CODE` links resolve to `/products/<id>` so the card shows the product.
**Caveat:** real social crawlers won't fetch the OG image over the internal-CA HTTPS
(`192.168.3.140:8443`) — correct on a public domain + valid cert.

## Deferred (Phase 2)
Tier/per-category commission (only flat % now). Admin UI (menus/list/form for
affiliates, conversions, payouts, clicks) is included.
