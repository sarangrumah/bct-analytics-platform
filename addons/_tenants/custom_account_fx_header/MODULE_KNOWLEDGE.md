---
status: draft
generated_at: 2026-08-04T00:00:00Z
generator: hand-authored
module: custom_account_fx_header
manifest_version: 19.0.1.1.0
---

# custom_account_fx_header

## Purpose
Puts the **foreign-currency total** and the **applied exchange rate** into the
header of a customer invoice / vendor bill whose currency differs from the
company currency, so an approver sees both without scrolling and without
mentally inverting a rate. Since 19.0.1.1.0 the same context is added to the
**Register Payment** popup. Display only — no posting, amount, or rate used by
the accounting engine is changed, and there is no schema change.

## Why it exists
Stock Odoo 19 already stores the rate on `account.move.invoice_currency_rate`
and renders it next to the currency in the header, but three things make it
unusable for this tenant:

1. It is printed in the `1 <company currency> = N <foreign>` direction —
   `1 IDR = 0.00037421 CNY`. IDR-based books quote the other direction
   (`1 CNY = 2,672.30 IDR`).
2. The document total in the foreign currency only appears at the bottom of the
   Invoice Lines tab.
3. The native block sits behind `base.group_multi_currency`, so it vanishes for
   any role that does not imply that group. The block added here carries no
   `groups` restriction.

## Business Flow
1. A user opens an invoice/bill written in a non-company currency.
2. `x_fx_is_foreign` computes True, revealing an `alert alert-info` block
   injected directly after the `oe_title` div (i.e. under the document number).
3. The block renders `amount_total` with the `monetary` widget (already in the
   document currency) and the rate as
   `1 <currency_id> = <x_fx_rate_company_per_unit> <company_currency_id>`.
4. On a same-currency document, or on a `move_type == 'entry'`, the block is
   invisible and the form is byte-for-byte the stock layout.

## Business Flow — Register Payment popup
The tenant bank journals (ids 51/53 on `prd_tenant`) have `currency_id`
explicitly set to IDR, so `_compute_currency_id` makes the wizard IDR and Odoo's
`_convert_to_wizard_currency` converts the CNY residual at the payment date.
That is correct, but the popup showed no trace of it — an IDR figure with no
CNY residual and no rate beside it is indistinguishable from a raw number.

1. `x_fx_is_foreign` computes True when `source_currency_id != currency_id`.
2. An `alert alert-info` block is injected before the first `group`, showing the
   open amount in the document currency, the rate in the readable direction, and
   the CNY the entered IDR amount actually settles (recomputed as `amount` is
   edited, so a partial payment shows a partial CNY figure).
3. If no `res.currency.rate` row exists for the document currency in that
   company, `res.currency._convert` silently falls back to 1.0 and a CN¥ 20,000
   bill proposes **Rp 20,000**. `x_fx_rate_missing` detects this and swaps the
   info block for an `alert alert-danger` telling the user to add the rate.
   This is the real defect behind the July-2026 "payment popup uses IDR and
   ignores the CNY" report: the only CNY rate row on `prd_tenant` was created
   on 2026-08-04, after the user tested.

## Key Models
- `account.move` (inherited) — adds two non-stored computed display helpers.
  No overrides of any core method.
- `account.payment.register` (inherited `TransientModel`) — adds four non-stored
  computed display helpers. No overrides of any core method.

## Important Fields
- `account.move.x_fx_is_foreign` (Boolean, computed, **non-stored**) — True when
  `move_type` is in `out_invoice/out_refund/out_receipt/in_invoice/in_refund/
  in_receipt` **and** `currency_id != company_currency_id`. Journal entries
  (`entry`) are deliberately excluded: a raw entry has no single document
  currency, so the block would be misleading.
- `account.move.x_fx_rate_company_per_unit` (Float, `digits=(16, 4)`, computed,
  **non-stored**) — `1 / invoice_currency_rate`, i.e. how many units of company
  currency one unit of the document currency is worth. Guarded against a zero
  rate (returns 0.0 rather than raising `ZeroDivisionError`); real data with
  `invoice_currency_rate == 0.0` exists on `trn_tenant`.
- `account.payment.register.x_fx_is_foreign` (Boolean) — `source_currency_id`
  differs from `currency_id`.
- `account.payment.register.x_fx_rate_payment_per_unit` (Float, `digits=(16, 4)`)
  — `source_currency._convert(1.0, currency_id, company, payment_date,
  round=False)`, i.e. payment-currency units per one document-currency unit.
- `account.payment.register.x_fx_amount_source` (Monetary on
  `source_currency_id`) — `amount` converted back into the document currency.
- `account.payment.register.x_fx_rate_missing` (Boolean) — no
  `res.currency.rate` row for the document (or payment) currency in that
  company. The company currency itself never needs one.

## Public / Overridden Methods
None. Only `@api.depends` computes plus one helper:
- `_compute_x_fx_is_foreign()` — depends on `move_type`, `currency_id`,
  `company_currency_id`.
- `_compute_x_fx_rate_company_per_unit()` — depends on `invoice_currency_rate`.
- `_compute_x_fx_values()` (wizard) — depends on `source_currency_id`,
  `currency_id`, `company_id`, `payment_date`, `amount`.
- `_x_fx_has_rate(currency, company)` — `sudo()` `search_count` on
  `res.currency.rate` with `company_id in (company.id, False)`.

## Views
- `views/account_move_views.xml` inherits `account.view_move_form` and xpaths
  `//div[hasclass('oe_title')]` `position="after"`. Block `name="tenant_fx_header"`.
- `views/account_payment_register_views.xml` inherits
  `account.view_account_payment_register_form`, xpaths `//group[1]`
  `position="before"`. Blocks `name="tenant_fx_payment_rate"` and
  `name="tenant_fx_payment_rate_missing"`. The two flag fields are added after the
  first `//field[@name='currency_id']` (the invisible one in the header block) —
  the second occurrence is the one inside `amount_div`, which is gated by
  `base.group_multi_currency` and must not be touched.

## Gating & Scope
Tenant-scoped to the tenant DBs, enforced by *placement* (`addons/_tenants/`)
and by install, not by a company flag — the module is inert on any document
whose currency equals the company currency, so it is harmless if installed
elsewhere. `depends: ["account"]` only. Deployed on `prd_tenant` and
`trn_tenant`.

## Tests
No automated tests. Verified manually against a full clone of `prd_tenant`:
`BILL/2026/07/0002` renders 20,000.70 CNY at 2,672.2963, whose product
(53,447,796.61) matches the ledger's `amount_total_signed` (53,447,796.69) to
within rounding; an IDR customer invoice shows no block and an unchanged
layout; `get_view` as a plain accounting user still contains the block.

The payment block was verified against live `prd_tenant` in a rolled-back
`odoo shell` transaction: `BILL/2026/07/0002` (CN¥ 20,000) opens the wizard at
Rp 53,445,926.08 with `x_fx_rate_payment_per_unit == 2672.2963` and
`x_fx_amount_source == 20,000 CNY`; halving `amount` gives 10,000 CNY; deleting
the CNY `res.currency.rate` row flips `x_fx_rate_missing` to True and drops the
proposed amount to the raw 20,000.00 IDR.
