---
status: draft
generated_at: 2026-07-02T07:43:22Z
generator: bootstrap-v1
module: custom_accounting_reports
manifest_version: 19.0.0.15.0
---

# custom_accounting_reports

## Purpose
This module closes the Enterprise gap on Odoo CE `account_reports`. It provides a comprehensive suite of financial reports for the Custom Platform — P&L, Balance Sheet, Cash Flow (indirect method), General Ledger, Trial Balance, Partner Ledger, Partner Cards, Aged Receivable/Payable, Tax (PPN/PPh), Day/Cash/Bank Book, Journal Audit, Down-Payment (Uang Muka) ledger, Sales, and a tree-driven custom Financial Report. All reports are built on a single shared `custom.report.engine` AbstractModel and render to QWeb PDF/HTML or XLSX.

## Business Flow
1. **User Selection**: The user opens a report from the menu (e.g., Trial Balance, General Ledger).
2. **Wizard Input**: A transient wizard (`custom.report.*.wizard`, under `wizard/`) collects filters such as date range, companies, journals, accounts, partners, and posted-only.
3. **Report Generation**: The wizard normalises its fields into a `filters`/`options` dict and hands off to the matching report model, which runs parameterised SQL against `account_move_line` via the engine helpers and builds report lines.
4. **Export/View**: Output is rendered as a QWeb PDF/HTML report (via the shared dispatch model) or exported to XLSX. Each run is written to the `pdp.audit_log` audit trail.

## Key Models
All report models are `AbstractModel`s that inherit `custom.report.engine` (the architectural base), except the concrete `custom.report.financial` tree and the QWeb dispatch model.

- `custom.report.engine` — **AbstractModel base.** Filter normalisation, raw-SQL aggregation (`_get_account_balances`, `_get_move_lines_query`, `_sum_by_account`), XLSX export, render context, and PDP audit logging.
- `report.custom_accounting_reports.report_dispatch` — **AbstractModel.** QWeb report dispatcher; maps a `report_code` to the target report model and returns its computed context.
- `custom.report.general.ledger` — General Ledger.
- `custom.report.trial.balance` — Trial Balance (default dispatch target).
- `custom.report.profit.loss` — Profit & Loss, bucketed by `account.group` (GROUP 1 code prefix), falling back to `account_type`.
- `custom.report.profit.loss.branch` — Profit & Loss with one amount column per branch; inherits `custom.report.profit.loss`. Reached from the P&L wizard's *View / Export by Branch* buttons (it owns no wizard, so no tenant needs a schema upgrade).
- `custom.report.balance.sheet` — Balance Sheet (Asset / Liability / Equity by account type, nested by `account.group`), including a computed **Current Year Earnings** equity line.
- `custom.report.cash.flow` — Cash Flow Statement (indirect method).
- `custom.report.partner.ledger` — Partner Ledger.
- `custom.report.partner.card.base` — Partner card base, subclassed by `custom.report.payable.card` and `custom.report.receivable.card`.
- `custom.report.aged.receivable` — Aged Receivable; `custom.report.aged.payable` inherits it.
- `custom.report.advance` — Uang Muka / Down-Payment ledger (auto-detects advance accounts).
- `custom.report.sales` — Sales report.
- `custom.report.tax` — Tax report (PPN / PPh subtotals; cross-references Coretax).
- `custom.report.book.mixin` — Day/Cash/Bank book mixin, subclassed by `custom.report.day.book`, `custom.report.cash.book`, `custom.report.bank.book` (three distinct models).
- `custom.report.journal.audit` — Journal Audit.
- `custom.report.financial` — **Concrete `models.Model`.** The only ORM model with stored fields; a self-referential tree defining custom financial-report line structure. Rendered by the `custom.report.financial.renderer` AbstractModel.

## Important Fields
Report models are AbstractModels and generally have no stored fields; user input lives on the transient wizards under `wizard/`.

- **custom.report.financial** (the only model with fields)
  - `parent_id` / `child_ids`: self-referential tree (`custom.report.financial`).
  - `account_ids`: `Many2many` to `account.account`.
  - `company_id`: `Many2one` to `res.company`.
  - `code`, `name`: used to compute the display name `[code] name`.

- **custom.report.advance.wizard**
  - `account_ids`: `Many2many` to `account.account`.
  - `company_ids`: `Many2many` to `res.company`.
  - `date_from`, `date_to`: report date range.
  - `posted_only`: `Boolean` (default `True`).

- **custom.report.aged.receivable.wizard / aged.payable.wizard**
  - `partner_ids`: `Many2many` to `res.partner`.
  - `detail_mode`: `Selection` switching summary vs. detail layout, **defaulting to `detail`**. (Note: `aging_detail` is only a **context key** derived from `detail_mode == "detail"`, not a field.)

- **Cash Flow bucketing** — `custom.report.cash.flow` has no `account_type` field. Buckets are defined by module-level tuples `OPERATING_TYPES`, `INVESTING_TYPES`, `FINANCING_TYPES`, `CASH_TYPES` matched against each row's Odoo `account_type`.

## Public Methods
- **Wizards** (entry points, e.g. `custom.report.advance.wizard`)
  - `action_print()`: renders the report to QWeb PDF/HTML.
  - `action_export_xlsx()`: exports to XLSX.

- **custom.report.engine** (shared, inherited by all report models)
  - `_default_filters()` / filter normalisation.
  - `_get_account_balances(filters)`, `_get_move_lines_query(...)`, `_sum_by_account(...)`: raw-SQL aggregation helpers.
  - `_compute(options)`: builds the render context.
  - `_account_groups(account_ids)`: two-level `account.group` ancestry per account; `{}` when the database defines no groups (the signal to fall back to `account_type`).
  - `_grouped_section(label, rows)`: nests signed rows under their GROUP 2 headers; degrades to a flat `accounts` list without groups.
  - `_branch_plan()`: the `account.analytic.plan` carrying the branch dimension, named by the `custom_accounting_reports.branch_plan_name` config parameter (default `Operating Unit`).
  - `_log_report_run(...)`: writes a run record to `pdp.audit_log`.
  - `get_report_table(options, context_extra)`: payload for the `custom_report_table` OWL client action — `columns` from `_xlsx_columns()` plus display `lines` from `_flatten_for_screen()`.
  - `_flatten_for_screen(lines, columns)` / `_screen_row(...)`: turn `_build_lines()` output into flat display rows. The default passes rows straight through, which suits reports whose lines are already flat.
  - `_flatten_grouped(lines, columns, group_type, heading, totals, total_label=None, opening_field=None)`: the flattener for reports that nest their movements under a group row. Mirrors their `_xlsx_body`: group heading (with the opening figure), the movements, the group total, then the grand total.

- **Report models**
  - `_build_lines(filters)`: overridden per report to build its lines (e.g. `custom.report.aged.receivable._build_lines`).
  - `_xlsx_body(sheet, ctx, columns, fmts, start_row)`: writes the Excel sheet; the grouped reports walk their nested lines here by hand.
  - `_flatten_for_screen(...)`: overridden by `custom.report.general.ledger` (grouped layout only), `custom.report.partner.ledger` and `custom.report.advance` to delegate to `_flatten_grouped`.
  - `custom.report.cash.flow._bucket(label, code, type_codes, balances, sign=-1)`: computes an activity bucket.

## Integration Points
- **Depends on**: `custom_core`, `custom_pdp_audit`, `custom_accounting_full`, `account`
- **Architecture**: every report is an AbstractModel inheriting the shared `custom.report.engine`; only `custom.report.financial` is a concrete ORM model. No `account.move` inheritance.
- **External calls**: `_log_report_run` executes a raw SQL `INSERT INTO pdp.audit_log` on every report run (via `self.env.cr.execute`).
- **Wizards**: live under `wizard/` (singular). There is no `controllers/` directory.

## Gotchas
- All computation runs through the single `custom.report.engine` base; overriding `_build_lines` is the extension point, not adding fields.
- **Never bucket a P&L by `account_type` alone.** Indonesian charts type every cost-of-sales account plain `expense` (not `expense_direct_cost`), so a type-based split reports COGS as zero, files `income_other` under Revenue, and drops `expense_other` entirely. Section membership comes from the account-code prefix via `account.group`.
- `account.account.group_id` is **computed, not stored** in Odoo 19 (resolved from the code prefix). It cannot appear in a SQL join or an ORM domain — read it off a browsed recordset, as `_account_groups` does.
- `account.group` rows are company-scoped; `account.analytic.plan` is not, but its accounts are. Both are filtered against the active companies.
- This module ships to every tenant. Adding a field or a `TransientModel` forces an `-u` on **all** databases that have it installed, or their General Ledger wizard breaks and their autovacuum cron logs errors. Prefer context keys and buttons on existing wizards.
- **A report that nests its movements under a group row must override `_flatten_for_screen`.** `_xlsx_columns()` + `_build_lines()` feed both Excel and the screen, but `_xlsx_body` walks the nesting itself while the default flattener does not — so the export looks right while the on-screen table shows one blank row per group and no movements. Delegate to `_flatten_grouped` (GL grouped layout, Partner Ledger, Advance do).
- Inside those flatteners, use `self.env._("fmt %s", arg)` — the module-level `_()` sniffs the caller frame for a language and logs a warning with a full stack trace **per row** when called from a lambda.
- The `custom.report.advance` model auto-detects advance/down-payment accounts by name, which may not cover all chart-of-account edge cases.
- `custom.report.aged.payable` inherits `custom.report.aged.receivable`, reusing its layout logic.
- Every run performs a defensive raw-SQL insert into `pdp.audit_log`; failures are logged as warnings and do not block the report.
- **This module's groups sit on its own `custom_accounting_reports.res_groups_privilege_accounting_reports` privilege** ("Accounting Reports") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- No real-time reporting or live data updates; reports are computed on demand from posted/existing move lines.
- No direct integration with external tax-filing systems (the tax report only cross-references Coretax data).
