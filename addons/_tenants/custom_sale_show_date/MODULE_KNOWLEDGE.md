---
status: draft
generated_at: 2026-06-09T00:00:00Z
generator: hand-authored
module: custom_sale_show_date
manifest_version: 19.0.1.5.0
---

# custom_sale_show_date

## Purpose
Adds a **Show Date** to the sale → invoice flow for opt-in companies (PT the tenant)
and anchors customer-invoice payment-term due dates to the show date instead of
the invoice date. Gated by a `res.company` boolean flag so it is safe on a
multi-company tenant DB (e.g. the tenant + the tenant): only the flagged company is affected.

## Business Flow
1. An operator ticks `res.company.x_custom_show_date_enabled` on the PT the tenant
   company (Settings → Companies → PT the tenant → "Show Date" page).
2. On a quotation, `x_custom_show_date` becomes required — but only when the
   order's company has the flag on (enforced server-side at confirm).
3. On confirmation the date stays on the Sales Order (`copy=True`).
4. `sale.order._prepare_invoice()` copies `x_custom_show_date` onto the customer
   invoice (`account.move`, `out_invoice`).
5. `account.move._compute_needed_terms` is overridden: for an `out_invoice` of a
   flagged company with a show date set, it re-runs the core compute on the move
   with context `tenant_show_date_ref=<show_date>`.
6. `account.payment.term._compute_terms` reads that context key and substitutes
   it for `date_ref`, so every `date_maturity` and the early-payment
   `discount_date` are anchored to the show date. Non-flagged companies are
   untouched (pure pass-through).

## Event Description Block (1.3+)
The order also captures `x_custom_event_name`, `x_custom_event_location` and
`x_custom_dp_note`. `sale.order._custom_event_description()` joins them with the
show date (`dd.mm.yy`) into one string, which `sale.order.line._compute_name()`
appends as a second line under every product line's description. It reaches the
customer invoice through the standard `_prepare_invoice_line`.

## Down-Payment Line Description (1.4+, event detail added in 1.5)
`sale.advance.payment.inv._prepare_down_payment_invoice_line_values()` replaces
core's "Down payment of 50.00%" with
`sale.order._custom_down_payment_description(marker)`:

    <product names>, <event block> (Uang Muka 50%)

Both the invoice PDF and the Faktur Pajak read this one stored `name` — the
coretax exporter uses `line.product_id.name or line.name`, and a DP line has no
product — so rewriting it fixes both printouts without touching the shared
`custom_report_templates` / `custom_coretax_export` addons.

Two constraints hold this shape:
- **Single line, always.** The string lands in one cell of the coretax import
  file, where an embedded newline is not safe. The event block is therefore
  taken once from the order, not from each product line's multi-line `name`.
- **`x_custom_dp_note` is excluded** here (`include_dp_note=False`), because the
  trailing marker already states the down payment; including it would print
  "DP 50% (Uang Muka 50%)". A fixed-amount DP gets `(Uang Muka)`, no percentage.

## Key Models
- `res.company` (inherited) — `x_custom_show_date_enabled` (Boolean gate flag).
- `sale.order` (inherited) — `x_custom_show_date` (Date),
  `x_custom_show_date_required` (computed view-driver). Overrides
  `_confirmation_error_message`, `_prepare_invoice`.
- `account.move` (inherited) — `x_custom_show_date` (Date). Overrides
  `_compute_needed_terms`.
- `account.payment.term` (inherited) — overrides `_compute_terms`.

## Important Fields
- `res.company.x_custom_show_date_enabled` (Boolean, default False) — gate.
- `sale.order.x_custom_show_date` (Date, copy=True, tracking=True).
- `sale.order.x_custom_show_date_required` (Boolean, computed, non-stored) —
  related to `company_id.x_custom_show_date_enabled`; drives view
  required/invisible attrs.
- `account.move.x_custom_show_date` (Date, copy=False).

## Public / Overridden Methods
- `sale.order._confirmation_error_message()` — blocks confirm when the flag is
  on and no show date is set (does not block drafts).
- `sale.order._prepare_invoice()` — adds `x_custom_show_date` to invoice vals.
- `account.move._compute_needed_terms()` — re-declares the core `@api.depends`
  (`invoice_payment_term_id`, `invoice_date`, `currency_id`,
  `amount_total_in_currency_signed`, `invoice_date_due`) plus `x_custom_show_date`
  and `company_id.x_custom_show_date_enabled`; injects the anchor context.
- `account.payment.term._compute_terms(date_ref, *args, **kwargs)` — consumes
  the `tenant_show_date_ref` context key.

## Gating & Scope
the tenant-only via the `res.company` flag (NOT name, NOT install). Customer invoices
only (`out_invoice`); vendor bills unaffected. Safe on multi-company / multi-
tenant DBs.

## Tests
`tests/test_show_date.py` (`AccountTestInvoicingCommon`): propagation SO→invoice,
required-only-when-flag-on, due date anchored to show date (show+30, not
invoice+30), and flag-off falls back to invoice-date anchoring.
