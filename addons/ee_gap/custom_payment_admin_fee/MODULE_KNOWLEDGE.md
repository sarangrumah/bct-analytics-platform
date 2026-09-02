---
status: draft
generated_at: 2026-08-04T00:00:00Z
generator: claude-code-handwritten
module: custom_payment_admin_fee
manifest_version: 19.0.1.1.0
---

# custom_payment_admin_fee

## Purpose
Lets a payment carry bank/admin charges on top of the document it settles, each charge booked to its own COA. Tenant-neutral extraction of feature #8 of `custom_retail_localization`, built so tenants other than the apparel brand (first consumer: the tenant) get the fee lines without inheriting the apparel brand card-BIN/MDR and Operating-Unit machinery.

## Business Flow
- On a posted bill/invoice, `Register Payment` opens `account.payment.register`. The form gains an **Admin Fees** group (above the footer) where the user adds one or more fee lines: label, fee account, amount.
- `_onchange_admin_fee_line_ids` recomputes `amount = <batch residual> + Σ fees` from the batch on every change, so amounts never accumulate and clearing the lines restores the plain residual.
- **Several bills at once** — the common case, since batching bills into one transfer is exactly what saves the fee. Adding a fee line ticks *Group Payments*, so one payment settles every selected bill and the fee is charged once for the whole transfer. The form explains this inline; unticking *Group Payments* while fees exist raises rather than splitting the fee across bills.
- On confirm, `_create_payment_vals_from_wizard` replaces the native single-line write-off with one write-off val per fee, so a 1,000,000 bill with a 1,500 fee posts `Dr Payable 1,000,000 / Dr Fee COA 1,500 / Cr Bank 1,001,500` and the bill still reconciles in full.
- A **negative** amount nets the fee off an inbound receipt: `Dr Bank (net) / Dr fee / Cr Receivable` — the usual booking for transfer/acquirer charges deducted before settlement.

## Key Models
- `payment.register.admin.fee` (TransientModel) — one admin-fee line on the wizard; `ondelete="cascade"` to the wizard.
- `account.payment.register` (`_inherit`) — hosts the O2m, the total, the amount recomputation and the write-off generation.

## Important Fields
- `payment.register.admin.fee`: `wizard_id` (M2o account.payment.register, required), `company_id`/`currency_id` (related from the wizard), `name` (Char, default "Admin Fee"), `account_id` (M2o account.account, required; domain excludes `asset_receivable`/`liability_payable`/`off_balance` and filters on `company_ids`), `amount` (Monetary, required, may be negative).
- `account.payment.register`: `admin_fee_line_ids` (O2m), `admin_fee_total` (Monetary, computed from the line amounts).

## Public Methods
- `_compute_admin_fee_total()` — sum of the line amounts.
- `_onchange_admin_fee_line_ids()` — recomputes `amount` from `_get_total_amounts_to_pay(batches)["amount_by_default"]` plus the fees, and sets `group_payment = True` when fees are present and `can_group_payments` (core leaves `group_payment` False for a multi-bill selection).
- `_compute_show_payment_difference()` (override) — forces `show_payment_difference = False` whenever fee lines exist, so the native single-account write-off UI cannot be used at the same time.
- `_prepare_admin_fee_write_off_vals()` — one `account.move.line` val per fee (`sign = +1` outbound, `-1` inbound), with `amount_currency` and a company-currency `balance` converted at `payment_date`.
- `_create_payment_vals_from_wizard()` (override) — asserts the balance, then sets `write_off_line_vals`.
- `_create_payment_vals_from_batch()` (override) — raises `UserError` if fees were entered on this (ungrouped multi-partner) path.
- `_assert_admin_fee_balance()` — raises unless `payment_difference + admin_fee_total` rounds to zero.

## Integration Points
- **Depends on:** `account` only.
- **Inherits:** `account.payment.register`; view inherits `account.view_account_payment_register_form` (`//footer` position="before").
- Fees ride Odoo's native `write_off_line_vals` channel, so the lines are ordinary journal items and appear on any payment voucher/receipt report without extra work.
- Security: `account.group_account_invoice` on `payment.register.admin.fee`.
- No cron, no `ir.config_parameter`, no `res.config.settings`, no data files.

## Gotchas
- **Never install alongside `custom_retail_localization`.** Both inherit the same wizard and both inject an "Admin Fees" group, so the section renders twice and the two `_onchange` handlers fight over `amount`.
- Fees only work when the wizard amount is editable — a single document, or several with *Group Payments* ticked (adding a fee ticks it automatically). The multi-partner batch path raises rather than silently dropping the fees.
- Selecting bills from **different partners** builds more than one batch, so `can_edit_wizard` is False and the whole section stays hidden — there is no single payment to attach a fee to. Register per partner instead.
- Hand-editing `Amount` after adding fees raises a `UserError`; the guard exists because a drifting amount would over/under-pay the bill and mis-reconcile it silently.
- The sign convention mirrors core write-off handling: outbound fee = debit, inbound mirrors it. A negative amount on an inbound receipt therefore still lands as a *debit* on the fee account while shrinking the cash-in.
- The fee account domain filters on `company_ids` (Odoo 19 multi-company accounts), not `company_id`.

## Out of Scope
- No card-BIN / MDR resolution (`retail.mdr.bin`) and no Operating-Unit analytic stamping — those stay tenant-specific in `custom_retail_localization`.
- No tax handling on the fee lines, no automatic fee suggestion per journal/bank, and no per-partner fee defaults.
