# custom_petty_cash — Module Knowledge

## Purpose
Full petty-cash cycle: request → approval → Bank-Out disbursement → realization
(third-party vendor bill *or* plain expense) → return/reimburse → settlement,
with Outstanding & Aging monitoring.

## Models
- `petty.cash.request` — pengajuan + pencairan + settlement. Inherits
  `mail.thread`, `mail.activity.mixin`, `approval.mixin`, `pdp.audited.mixin`.
  State: `draft → to_approve → approved → disbursed → in_realization → settled`
  (+ `cancelled`). `amount_outstanding` is the net balance of advance-account
  lines tagged to the request (`account.move.petty_cash_request_id`).
- `petty.cash.request.line` — optional estimate breakdown.
- `petty.cash.realization` — pertanggungjawaban; `action_post` builds the GL.
- `petty.cash.realization.line` — `line_type` `third_party` / `expense`.
- `account.move` — tagged with `petty_cash_request_id` / `petty_cash_realization_id`.
- `res.company` / `res.config.settings` — accounts + journals + params.
- `petty.cash.report.outstanding` / `petty.cash.report.aging` — subclass
  `custom.report.engine`; registered in `REPORT_MODEL_MAP` (see
  `models/report_dispatch.py`) so PDF/XLSX/on-screen table all work.

## Accounting
| Step | Dr | Cr |
|---|---|---|
| Disburse | Advance (employee) | Bank |
| 3rd-party bill | Expense+PPN | AP |
| 3rd-party PPh (custom_tax_id, auto) | AP | Hutang PPh |
| 3rd-party pay (Payment journal) | AP | Advance |
| Expense | Expense (COA) | Advance |
| Return | Bank | Advance |
| Reimburse | Advance | Bank |
| Settle | *advance lines reconcile to zero* | |

The Payment journal's payment-method `payment_account_id` is auto-set to the
advance account (`_configure_payment_journal`), so `account.payment.register`
credits the advance instead of a bank outstanding account.

## Reuse
- Approval: `approval.mixin` (custom_approval_engine) — `action_submit` calls
  `_approval_request_or_proceed`; `_approval_on_granted` → `action_approve`.
- Withholding/PPN: set `tax_ids` + `x_custom_withholding_category_id` on the
  bill line; `custom_tax_id.account_move._post` applies PPh + bukti potong.
- Reports: `custom.report.engine` / `custom.report.wizard.mixin` /
  `REPORT_MODEL_MAP` in custom_accounting_reports; aging reuses its `BUCKETS`.
- OU: `l10n_ou_analytic_id` merged into `analytic_distribution` (guarded on
  `_fields` so it's a no-op without the localization).

## Gotchas
- Requires Advance account + Bank-Out + Payment journals configured before
  disbursing / posting third-party lines (raises `UserError` otherwise).
- `report_dispatch.py` mutates the shared `REPORT_MODEL_MAP` at import — the
  addon is shared, so both report codes appear on every DB that installs this.
- PDF routing added by inheriting `custom_accounting_reports.report_dispatch`
  (self-contained `<main>` wrapper — no `web.html_container`, per the platform
  wkhtmltopdf-stall note).
