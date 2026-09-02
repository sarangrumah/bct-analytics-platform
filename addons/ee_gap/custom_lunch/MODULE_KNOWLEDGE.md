---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_lunch
manifest_version: 19.0.0.2.0
---

# custom_lunch

## Purpose
EE-equivalent Indonesia extensions on top of CE `lunch`: deep-link supplier integration with **GoFood / GrabFood / ShopeeFood** (computed `x_partner_app_url` from merchant ids), halal certification + spice level + calorie + day-of-week scheduling on products, daily auto-publish cron flipping `lunch.product.active` based on a `mon,tue,wed,...` CSV, monthly cron aggregating confirmed lunch orders into payroll deductions on the matching `hr.payslip`.

## Business Flow
- HR sets up `lunch.supplier` with `x_id_vendor_type` (walking/delivery/gofood/grabfood/shopeefood/direct), per-vendor merchant id (`x_id_gofood_id` etc.), `x_id_halal_certified`, `x_id_min_order`. `_compute_partner_app_url` builds the public deep link from the matching template (e.g. `https://gofood.co.id/restaurant/{merchant_id}`).
- "Open" button on supplier form calls `action_open_vendor_app()` returning an `ir.actions.act_url` (`target='new'`). Raises if no merchant id configured.
- HR creates `lunch.product` rows with `x_id_halal`, `x_id_vegetarian`, `x_id_spice_level` (none/mild/medium/hot/very_hot), `x_id_calories`, and optional `x_available_days` CSV like `"mon,wed,fri"`. `@api.constrains` validates day tokens (first 3 chars must be in `mon/tue/wed/thu/fri/sat/sun`).
- Daily cron `lunch.product.cron_publish_daily_menu` (per `data/lunch_cron.xml`): for products with a non-empty `x_available_days`, set `active = today's weekday token IN allowed`. Uses `with_context(active_test=False)` so archived rows can be re-activated. Empty schedule = always-on (left untouched).
- Employees place `lunch.order` records (CE flow) with `x_payroll_deduction=True` (default).
- Monthly cron `lunch.order.cron_aggregate_lunch_to_payroll`: aggregates the previous calendar month's `confirmed`/`ordered` orders where `x_payroll_deduction=True` and `x_payslip_id=False`, sums `price` per `order.user_id.employee_id`. **Currently a stub** — only logs `[custom_lunch] Payroll aggregation ...` per employee; the manifest description claims it posts a "Lunch Deduction" line on the draft payslip and links back via `x_payslip_id`, but the implementation has a TODO and performs no writes.

## Key Models
- `lunch.supplier` (inherited) — Vendor type, merchant ids per board, halal flag, computed deep-link URL.
- `lunch.product` (inherited) — Halal/vegetarian flags, spice level, calories, day-of-week CSV.
- `lunch.order` (inherited) — Payroll deduction toggle + payslip link.

## Important Fields
- `lunch.supplier.x_id_vendor_type` (Selection: walking/delivery/gofood/grabfood/shopeefood/direct, default direct).
- `lunch.supplier.x_id_halal_certified` (Boolean).
- `lunch.supplier.x_id_min_order` (Monetary, currency=`x_id_currency_id`).
- `lunch.supplier.x_id_gofood_id` / `x_id_grabfood_id` / `x_id_shopeefood_id` (Char) — merchant ids.
- `lunch.supplier.x_partner_app_url` (Char, computed, **stored**) — deep link; URL-quoted merchant id; empty if vendor type not in templates or no merchant id.
- `lunch.product.x_id_halal` / `x_id_vegetarian` (Boolean).
- `lunch.product.x_id_spice_level` (Selection: none/mild/medium/hot/very_hot, default none).
- `lunch.product.x_id_calories` (Integer kcal).
- `lunch.product.x_available_days` (Char) — CSV `"mon,tue,..."`; empty means always-on.
- `lunch.order.x_payroll_deduction` (Boolean, default True).
- `lunch.order.x_payslip_id` (M2o `hr.payslip`, readonly) — currently never written by the stub cron.

## Public Methods
- `lunch.supplier.action_open_vendor_app()` — Returns `ir.actions.act_url` (vendor app URL); raises if no merchant id.
- `lunch.product.cron_publish_daily_menu(today=None)` (`@api.model`) — Activate/deactivate products by weekday; returns `{scanned, activated, deactivated}`.
- `lunch.order.cron_aggregate_lunch_to_payroll()` — Stub aggregation by employee for previous month.
- Module-level helpers: `_parse_days_csv(value)`, `_VENDOR_URL_TEMPLATES`, `_VENDOR_LABELS`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `lunch`, `custom_hr_payroll_id`.
- **Inherits from:** `lunch.supplier`, `lunch.product`, `lunch.order`.
- **Extended by:** none in-tree.
- **External calls:** none server-side; the deep links open in user browsers.
- **Cross-vertical:** Indonesia-specific (uses gofood.co.id / food.grab.com/id / shopeefood.co.id hosts and the `62` country code is not used here, but the regional URLs are).

## Gotchas
- **Payroll-deduction cron is a stub** — `cron_aggregate_lunch_to_payroll` logs intent but **does not create `hr.payslip.line` rows or link `x_payslip_id`**, despite the manifest description claiming so. BRD reviewers must not assume monthly lunch deductions actually post to payroll yet.
- **Day-of-week tokens are first-3 chars lower-cased** — `"Monday"` is normalised to `"mon"` (accepted); `"M"` is rejected. The constrains validates first 3 chars; the parser also uses first 3 chars.
- **`cron_publish_daily_menu` toggles `active` — archives products** that should be off-day, which makes them disappear from default search domains. The next-day cron re-activates them. Hostile to manual `active=False` curation since the cron will overwrite.
- **Empty `x_available_days` means "always-on"** and is **never archived** by the cron, but also can't be archived without being re-activated on a matching day.
- **PDP audit is in `depends` but the module does NOT actually write to `pdp.audit_log`** — included in depends for retention/access policy alignment, not for explicit audit hooks here.
- **Vendor URL templates are hardcoded** — if GoFood/GrabFood/ShopeeFood change URL schemes, code must be updated.
- **Merchant id is URL-quoted with `safe=""`** so even `/` in an id gets escaped — confirms the merchant id is expected to be path-safe text.
- **`x_payslip_id` is a M2o but readonly with no setter pathway** — there's no public method to attach a lunch order to a payslip beyond the (unimplemented) cron.
- **Order `state in ['confirmed', 'ordered']`** is the aggregation filter — depends on CE `lunch.order.state` not being customised away from these values.
- **`order.price` is the source value** — assumes the CE `lunch.order` has a `price` Float field (it does in 19.0).

## Out of Scope
- **Real payroll deduction posting** — stub only.
- **Halal certificate document storage** — only a Boolean flag, no attachment.
- **Vendor app native deep links** (`gojek://`, `grab://`) — uses HTTPS URLs that mobile OSes intercept.
- **Per-employee quota / monthly cap** — no max-spend limits.
- **Multi-currency lunch** — supplier has `x_id_currency_id` but cross-currency aggregation isn't reconciled.
- **Weekly calorie summary view/report** — claimed in the manifest description but only the field exists; no SQL view model or dedicated report in this directory.
- **PDP audit hooks** — included via depends but no explicit `_pdp_audit_*` calls in code.
