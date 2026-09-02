# Custom Petty Cash

Employee petty-cash advances with a full Finance cycle on Odoo 19 Community.

## Flow

1. **Request** (`petty.cash.request`) — an employee asks for cash for an
   Operating Unit, optionally with an estimate breakdown.
2. **Approval** — routed through `custom_approval_engine`'s matrix; when no
   matrix matches, a Finance user approves directly.
3. **Disbursement (Bank Out)** — Finance disburses the approved amount:
   `Dr Uang Muka Petty Cash (employee) / Cr Bank`.
4. **Realization** (`petty.cash.realization`) —
   * *Third-party* lines → a vendor bill (`in_invoice`) carrying PPN (`tax_ids`)
     and PPh (`x_custom_withholding_category_id`, applied by `custom_tax_id`),
     then paid out of the advance through the Petty Cash payment journal
     (`Dr AP / Cr Uang Muka`). The supplier invoice attachment is mandatory.
   * *Expense* lines → `Dr Expense / Cr Uang Muka` in one entry.
5. **Return / Reimburse / Settle** — leftover cash is returned
   (`Dr Bank / Cr Uang Muka`) or a shortfall reimbursed
   (`Dr Uang Muka / Cr Bank`); when the advance nets to zero the request is
   settled and the advance lines are reconciled.
6. **Monitoring** — kanban/list dashboards, an **Outstanding** ledger and an
   **Aging** report (PDF / XLSX / on-screen table via `custom_accounting_reports`).

## Configuration (Accounting → Settings → Petty Cash)

| Setting | Purpose |
|---|---|
| Advance Account | Reconcilable "Uang Muka Petty Cash" (per employee). |
| Bank-Out Journal | Funds disbursement / receives returns. |
| Payment Journal | Pays third-party bills out of the advance (its outstanding accounts are auto-pointed at the advance account). |
| Expense Journal | Miscellaneous journal for expense entries (falls back to the first `general` journal). |
| Realization Deadline (days) | Default deadline after disbursement; drives the overdue filter + reminder cron. |
| Disburse via account.payment | Book disbursement as a bank-reconcilable payment instead of a direct entry. |

## Operating Unit

The `l10n_ou_analytic_id` field (analytic plan "Operating Unit") is stamped
onto every generated journal item's `analytic_distribution`; when the apparel brand
localization is installed the native `l10n_ou_analytic_id` line field is set
too. Without the localization the module runs unchanged.

## Security

* **Petty Cash / User** — employees: own requests/realizations only.
* **Petty Cash / Finance** — review, approve, disburse, post, settle, reports.
