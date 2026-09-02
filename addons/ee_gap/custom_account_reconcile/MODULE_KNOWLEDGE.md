---
status: draft
generated_at: 2026-07-24T00:00:00Z
generator: claude-code-handwritten
module: custom_account_reconcile
manifest_version: 19.0.2.0.0
---

# custom_account_reconcile

## Purpose
Supplies the manual-reconciliation UI that Odoo Community lacks: an overview dashboard of reconcilable accounts with open items, a "Reconcile" wizard for selected journal items, and a bank-statement-line matching wizard. The reconciliation itself is delegated to core CE `account.move.line.reconcile()` / `account.bank.statement.line._reconcile_with_amls()` — this module is UI + candidate scoring, not a new engine.

## Business Flow
- **Overview**: `Accounting → Reconciliation → Reconcile` opens `action_custom_reconcile_overview` (list of `custom.reconcile.account`). Row button `action_open_lines` drills into that account's posted, unreconciled `account.move.line`s.
- **Journal-items reconcile**: the `account.move.line` list contextual action `action_custom_reconcile_lines` ("Reconcile") opens `custom.account.reconcile.wizard`. `default_get` validates the selection; `action_reconcile` reconciles directly when balanced, or in `writeoff` mode calls `_create_writeoff_line()` (posts a balancing entry) then `reconcile()`.
- **Bank matching**: `action_custom_bank_reconciliation` lists posted `account.bank.statement.line`s; per-row `action_open_match_wizard` opens `custom.bank.reconcile.wizard`; `action_reconcile` calls `st_line._reconcile_with_amls(...)`. The "Auto-match" server action `action_st_lines_auto_match` calls `records.action_auto_match()`.

## Key Models
- `custom.reconcile.account` (`_auto=False` SQL view) — one row per reconcilable account carrying posted unreconciled lines; `id = account.id`. Aggregates span ALL companies sharing the account.
- `custom.account.reconcile.wizard` (TransientModel) — manual reconcile of selected journal items.
- `custom.bank.reconcile.wizard` + `custom.bank.reconcile.wizard.line` (TransientModel) — bank-statement-line matching + candidate rows.
- `account.bank.statement.line` (`_inherit`) — candidate search + reconcile mechanics.

## Important Fields
- `custom.reconcile.account`: `account_id` (M2o account.account), `line_count` (Integer), `debit`/`credit`/`residual` (Monetary), `oldest_date` (Date), `currency_id` (computed from `env.company`, `@api.depends_context("company")`).
- `custom.account.reconcile.wizard`: `line_ids` (M2m account.move.line, readonly), `account_id`, `company_id`, `debit`/`credit`/`residual` (Monetary), `is_balanced` (Boolean), `mode` (`partial`/`writeoff`, default `partial`), `writeoff_account_id`/`writeoff_journal_id` (check_company, domain-restricted), `writeoff_date`, `writeoff_label` (default "Write-Off").
- `custom.bank.reconcile.wizard`: `st_line_id` (required), `candidate_ids` (O2m wizard.line), `selected_total`/`remainder` (Monetary computed), `writeoff` (Boolean), `writeoff_account_id` (domain excludes receivable/payable), `writeoff_label`.
- `custom.bank.reconcile.wizard.line`: `selected` (Boolean), `aml_id` (M2o account.move.line), `amount_residual` (related), plus related move/date/account/partner.

## Public Methods
- `custom.reconcile.account.init()` — (re)creates the SQL view (`WHERE aa.reconcile AND NOT ml.reconciled AND ml.parent_state='posted'`, grouped by account); `action_open_lines()` drill-down.
- `custom.account.reconcile.wizard.action_reconcile()` / `_create_writeoff_line()`.
- `custom.bank.reconcile.wizard.action_reconcile()`, `action_search_more()` (relaxed candidate refresh), `_candidate_commands()`, `_compute_amounts()`.
- `account.bank.statement.line._get_match_candidates(limit=30, relax=False)` (scored candidate AMLs), `_get_auto_match_candidate()` (unique exact hit), `_reconcile_with_amls(amls, writeoff_vals=None)`, `action_open_match_wizard()`, `action_auto_match()` (bulk auto-reconcile with notification tally).

## Integration Points
- **Depends on:** `account` only.
- **Inherits:** `account.bank.statement.line`. Reuses core `_seek_for_lines`, `_prepare_counterpart_amounts_using_st_line_rate`, `_get_default_amls_matching_domain`, `reconcile()`.
- Contextual/server actions bound to `account.model_account_move_line` and `account.model_account_bank_statement_line`.
- Security: `account.group_account_readonly` (overview read-only), `account.group_account_user` (wizards).
- No cron, no `ir.config_parameter`, no `res.config.settings`.

## Gotchas
- `custom.reconcile.account` is a SQL view (`_auto=False`); aggregate columns span all companies sharing an account — on multi-company DBs treat sums as indicative and the drill-down as authoritative. `residual = SUM(ml.amount_residual)`.
- `_reconcile_with_amls` guards: raises if the line is already reconciled, if it has no suspense leg, or if an AML is already reconciled; writes with `force_delete/skip_readonly_check/skip_account_move_synchronization` context; leftover remainder stays on suspense unless a write-off is supplied.
- Wizard `default_get` enforces ≥2 unreconciled lines, a single posted account with `reconcile=True`, and a single company.
- Write-off in the journal-items wizard only supports company-currency lines (foreign currency raises → use partial mode).
- Auto-match only fires on a unique exact-residual candidate (partner-agreement required when the statement line has a partner).

## Out of Scope
- No new reconciliation engine (delegates to core `reconcile()`), no own exchange-difference logic, no automatic/cron reconciliation, no bank statement import (see [[bank-import-bca-corp-csv]] / `custom_bank_import`), and write-off does not handle multi-currency.
