# the tenant Show Date (`custom_sale_show_date`)

Adds a **Show Date** to the sale → invoice flow for **PT the tenant only**, and anchors
customer-invoice payment-term due dates to the show date.

## What it does

1. **Quotation / Sales Order** (`sale.order`) gets `x_custom_show_date` (Date).
   It is **required** before confirming — but only for companies that have the
   feature enabled.
2. The date flows to the **Customer Invoice** (`account.move`, `out_invoice`)
   via `sale.order._prepare_invoice()`.
3. For enabled companies, the invoice **payment-term due dates are computed from
   the show date** ("X days after show date") instead of the invoice date.

## Gating — important

The feature is gated by a per-company flag
`res.company.x_custom_show_date_enabled` — **not** by company name and **not**
merely by installing the module. This makes it safe to install on a
**multi-company** tenant DB (e.g. the tenant + the tenant in one database): only the company
with the flag ticked (PT the tenant) is affected; every other company behaves exactly
like stock Odoo. Until the flag is ticked the module is inert.

## Install (the tenant DBs only)

Install on the tenant DBs (`uat_tenant`, `rnd_tenant`,
`prd_EAL_Tenant`) only. Do **not** install on other tenants.

After install, an operator must enable the gate:

> Settings → Companies → **PT the tenant** → **Show Date** page → tick
> **Enable Show Date (the tenant)** → Save. Leave the tenant company unticked.

## How the anchoring works (technical)

`account.move._compute_needed_terms` is overridden: for a flagged company's
customer invoice that has a show date, it re-runs the core compute with context
`tenant_show_date_ref=<show_date>`. `account.payment.term._compute_terms` reads
that context key and substitutes it for `date_ref`, so every `date_maturity`
(and the early-payment `discount_date`) is computed relative to the show date —
with no duplication of core term logic. The context key is only ever set by this
module's move override, so it is a pure pass-through for every other company,
module, and tenant.
