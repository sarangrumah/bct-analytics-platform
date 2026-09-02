---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_accounting_full
manifest_version: 19.0.0.3.0
---

# custom_accounting_full

## Purpose
Canonical multi-company accounting EE-gap module: closes the delta between Odoo CE `account` and the EE `account_consolidation` + `account_inter_company_rules` + `account_followup` + `account_3way_match` + `account_reconcile_oca` bundles. Owns the Indonesian PSAK-aligned chart template (`id_psak`), the intercompany mirror engine, two distinct consolidation models (perimeter-based + group-COA based), the fiscal-year lock workflow, bank-statement auto-reconcile rules, customer credit-limit enforcement, customer follow-up ladders, and 3-way matching (PO ↔ GRN ↔ vendor bill).

This is the umbrella accounting module — anything described in a BRD as "intercompany", "consolidation", "eliminations", "credit limit", "follow-up", "3-way match", "fiscal year close", "auto-reconcile" or "branch/cost-centre" lives here. Other ee_gap modules (`custom_accounting_asset`, `custom_accounting_recurring`, `custom_accounting_reports`) depend on this one.

## Business Flow
- **Indonesian COA** install: `account.chart.template` `@template("id_psak")` ships a 5-digit PSAK chart — 53 accounts (1xxxx Aset … 8xxxx Pajak Penghasilan), 12 account groups for hierarchical reports, PPN 11% keluaran/masukan taxes + 1 tax group (PMK 58/2022 rate), 6 journals (INV/BILL/CASH/BANK/MISC/EXCH) with Bahasa Indonesia labels, and 2 fiscal positions (Ekspor, Pelanggan Bebas Pajak). PPh withholding (23, 4(2), 26) intentionally deferred. Company onboarding selects this template from Settings → Accounting.
- **Intercompany mirror**: `account.move._post` calls `_custom_run_intercompany_mirror()`; if `partner_id.commercial_partner_id` matches another `res.company.partner_id` and an active `account.intercompany.rule` exists with matching `direction`, `_custom_create_intercompany_mirror(rule)` creates a draft `account.move` in the receiving company with mapped accounts (via `account.intercompany.account.mapping`) and links `x_custom_ic_mirror_id` ↔ `x_custom_ic_source_id`. Optional `auto_validate` posts the mirror.
- **Consolidation (perimeter style)**: an `account.consolidation.config` declares parent + subsidiaries + elimination accounts. `build_trial_balance(date_from, date_to)` runs `_compute_balances` (read_group on `account.move.line`) → `_compute_eliminations` → returns a pivoted dict per account with `by_company` columns + `elimination` + `consolidated`. Audit row written via `_audit_report_run`.
- **Consolidation (group-COA style)**: a `custom.consolidation.chart` declares a group-COA with `custom.consolidation.chart.account` children + per-company `custom.consolidation.mapping` (local `account.account` → group account, with `fx_method` and `weight`). State `draft`→`locked` via `action_lock`.
- **Elimination workflow**: `custom.elimination.rule` defines an account pair (`account_a_id` in `company_a_id` ↔ `account_b_id` in `company_b_id`); `custom.elimination.proposal.action_compute()` aggregates posted lines, fills `custom.elimination.proposal.line` rows, sets `state=proposed`. `action_post()` calls `_make_elimination_move()` → creates a balanced `account.move` debiting A / crediting B for `total_amount = min(|a_amount|, |b_amount|)`. `action_reject` / `action_cancel` provided.
- **Fiscal year**: `custom.fiscal.year` records (one per `company_id` × non-overlapping date range) progress `draft`→`open`→`closed`; close is run from `custom.fiscal.year.close.wizard`.
- **Bank auto-reconcile cron**: `custom.reconcile.rule._cron_auto_reconcile` walks unmatched `account.bank.statement.line` per company; each line calls `_custom_apply_reconcile_rules` → for each applicable `custom.reconcile.rule`, `_candidate_move_lines` searches receivable/payable AMLs within `match_date_window_days`, filtered by `_line_matches` (amount within `amount_tolerance`, regex match on `payment_ref/ref/narration`). Best candidate auto-reconciles when `rule.auto_validate=True`.
- **Customer credit limit**: `sale.order.action_confirm` is overridden to call `_custom_credit_check`; reads `partner.custom_credit_limit` + `custom_outstanding_amount`; if `projected > limit` and `method=='block'` raises `UserError`, if `'warn'` posts a chatter warning. Every check writes a `custom.credit.check.log` row.
- **Follow-up ladder**: `custom.followup.level._cron_apply_followup` queries partners with posted unreconciled receivable lines past `date_maturity`; per partner `_custom_advance_followup_level` bumps `custom_followup_level_id` to the highest matching `delay_days`, and `_custom_send_followup_email_if_due` dispatches `email_template_id` respecting `custom_followup_next_date` throttle (`max(7, delay_days/2)` days).
- **Unique vendor bill reference**: `account.move._post` for `in_invoice`/`in_refund` runs `_custom_find_duplicate_bill_ref` per move; if another non-cancelled move for the same `commercial_partner_id` + `company_id` + `move_type` already carries the same (stripped) `ref`, it raises `UserError`. This hard-blocks what Odoo CE only surfaces as the soft `duplicated_ref_ids` banner. Empty refs are never duplicates. Search runs in `sudo()` so a duplicate hidden by record rules still triggers the block.
- **3-way match**: `account.move._post` for `in_invoice` runs `_custom_run_three_way_match`. Per bill line with a `purchase_line_id`, computes qty variance vs `qty_received` and price variance vs PO `price_unit`; line `status` is `pass`/`qty_variance`/`price_variance`/`both`. Overall result stored on `custom.match.result` + `custom.match.line.result`. `policy.on_qty_mismatch` / `on_price_mismatch` ∈ {`warn`,`block`} — `block` raises `UserError` and prevents posting.
- **Analytic branch dim**: `account.analytic.account.x_custom_branch_code` + `x_custom_is_branch_root` + `x_custom_parent_id` + computed `x_custom_branch_root_id` (recursive) for kantor-cabang reporting (Odoo 19 no longer ships `account.analytic.account.parent_id`).

## Key Models
- `account.intercompany.rule` — Declarative mirror policy (`company_from_id` → `company_to_id`, `direction` ∈ `sale_to_purchase`/`purchase_to_sale`/`both`, `target_journal_id`, `auto_validate`).
- `account.intercompany.account.mapping` — Per-rule source→target `account.account` pairs; `_check_company_alignment` ensures accounts belong to the right company's chart.
- `account.consolidation.config` — Perimeter (parent + subsidiaries + elimination accounts + presentation currency); exposes `build_trial_balance`, `_compute_balances`, `_compute_eliminations`.
- `custom.consolidation.chart` — Group-COA root (`_check_company_auto=True`, state `draft`/`locked`, `mail.thread`).
- `custom.consolidation.chart.account` — Account in the group COA (`account_category` ∈ asset/liability/equity/income/expense/off_bs).
- `custom.consolidation.mapping` — Per-(`chart_id`,`company_id`,`source_account_id`) → group `target_account_id` with `fx_method` (`avg`/`closing`/`historical`) + `weight`.
- `custom.elimination.rule` — Eliminate `account_a_id` in `company_a_id` against `account_b_id` in `company_b_id`; optional `match_partner_id`, `threshold_amount`, legacy `match_type`.
- `custom.elimination.proposal` — Workflow `draft`/`proposed`/`posted`/`rejected`/`cancelled`; produces an `account.move` via `_make_elimination_move`.
- `custom.elimination.proposal.line` — Computed source-balance row (per company × account).
- `custom.fiscal.year` — Non-overlapping period per `company_id`; `draft`/`open`/`closed`.
- `custom.reconcile.rule` — Bank reconcile rule (journals, `match_partner`, `match_amount` + `amount_tolerance`, `match_reference_regex`, `match_date_window_days`, `payment_match_partner_field`, `auto_validate`).
- `account.bank.statement.line` (inherited) — Adds `custom_reconcile_rule_id` + `custom_auto_matched`.
- `res.partner` (inherited) — Adds `custom_credit_limit`, `custom_credit_limit_check_method`, computed `custom_outstanding_amount`/`custom_credit_available`; also `custom_followup_level_id`, `custom_followup_last_sent`, `custom_followup_next_date`, computed `custom_max_overdue_days`.
- `custom.credit.check.log` — Append-only audit row per `sale.order` credit check; `decision` ∈ pass/allowed/warn/warned/blocked.
- `sale.order` (inherited) — `action_confirm` calls `_check_credit_limit` → `_custom_credit_check`.
- `custom.followup.level` — Ladder rung (`delay_days`, `action`, `email_template_id`).
- `custom.followup.stat.by.partner` — `_auto=False` SQL view aggregating overdue per partner.
- `custom.match.policy` — `qty_tolerance_percent`, `price_tolerance_percent`, `on_qty_mismatch`/`on_price_mismatch`.
- `custom.match.result` / `custom.match.line.result` — Per-bill / per-line outcome.
- `account.move` (inherited) — Adds `x_custom_ic_mirror_id`, `x_custom_ic_source_id`, `x_custom_ic_rule_id`, `custom_match_result_id`, `custom_match_status`; inherits `pdp.audited.mixin`.
- `account.analytic.account` (inherited) — `x_custom_branch_code`, `x_custom_is_branch_root`, `x_custom_parent_id`, computed `x_custom_branch_root_id`.
- `res.company` (inherited) — `x_custom_ic_enabled` kill-switch; `_sister_companies()` helper.

## Important Fields
- `account.intercompany.rule.direction` (Selection) — drives `account.move._custom_find_intercompany_rule` matching against `move_type`.
- `account.intercompany.rule.auto_validate` (Boolean) — when True the mirror is posted automatically.
- `account.move.x_custom_ic_mirror_id` / `x_custom_ic_source_id` (M2o `account.move`) — idempotency guard; never re-mirror if `x_custom_ic_mirror_id` already set.
- `account.consolidation.config.elimination_account_ids` (M2m `account.account`) — accounts whose perimeter balances are netted; residual is the "elimination" column.
- `account.consolidation.config.presentation_currency_id` (M2o, required) — FX consolidation currency; FX rate methods are declared per-mapping not per-config.
- `custom.consolidation.mapping.fx_method` (Selection avg/closing/historical) — per-account FX conversion rule.
- `custom.consolidation.mapping.weight` (Float, default 1.0) — multiplier for partial ownership / JV consolidation.
- `custom.elimination.proposal.total_amount` (Monetary) — `min(|a_amount|, |b_amount|)`; the netting amount used for the elimination move.
- `custom.fiscal.year.state` (Selection draft/open/closed) — `_check_dates_and_overlap` blocks overlap on the same `company_id`.
- `custom.reconcile.rule.match_reference_regex` (Char) — Python regex tested against `payment_ref or ref or narration`; `_check_regex` compiles at constraint time.
- `custom.reconcile.rule.amount_tolerance` (Float) — absolute tolerance for `|stmt.amount| - |aml.amount_residual|` (defaults to 0.005 fudge inside `_line_matches`).
- `res.partner.custom_credit_limit_check_method` (Selection none/warning/block) — drives `_custom_credit_check` action.
- `custom.followup.level.delay_days` (Integer) — minimum overdue threshold; `_custom_advance_followup_level` picks highest matching tier.
- `custom.match.result.overall_status` (Selection: pass/match/qty_variance/qty_mismatch/price_variance/price_mismatch/both/both_mismatch/no_po/error) — exposed via related `account.move.custom_match_status`.
- `account.analytic.account.x_custom_branch_root_id` (M2o, recursive compute) — entire branch subtree resolution.

## Public Methods
- `account.move._post(soft=True)` — overridden in two classes: `account_move_bill_ref.py` runs `_custom_find_duplicate_bill_ref` (before super, raises on duplicate ref) and `account_move_match.py` runs `_custom_run_three_way_match` (before super, can raise) + `_custom_run_intercompany_mirror` (after super, never blocks). Both chain via `super()`.
- `account.move._custom_find_duplicate_bill_ref()` — returns other non-cancelled vendor bills sharing this Bill Reference for the same vendor/company; empty result for non-vendor-bills or blank refs.
- `account.move._custom_run_intercompany_mirror()` / `_custom_find_intercompany_rule()` / `_custom_create_intercompany_mirror(rule)`.
- `account.intercompany.rule._map_account(src_account)` — explicit mapping → same-code lookup → empty.
- `account.consolidation.config.build_trial_balance(date_from, date_to)` — pivoted consolidated TB.
- `account.consolidation.config._compute_balances(date_from, date_to, account_types=None)` / `_compute_eliminations(balance_rows)`.
- `account.consolidation.config._audit_report_run(kind, config, date_from, date_to, row_count)` (`@api.model`) — best-effort PDP audit.
- `account.consolidation.config.perimeter_company_ids()` — parent ∪ subsidiaries.
- `custom.consolidation.chart.action_lock()` / `action_reset_draft()`.
- `custom.elimination.proposal.action_compute()` / `action_post()` / `action_reject()` / `action_cancel()` / `_make_elimination_move()`.
- `custom.fiscal.year.action_open()` / `action_reset_draft()` / `action_open_close_wizard()`.
- `custom.reconcile.rule._cron_auto_reconcile()` (`@api.model`) — daily cron entry.
- `custom.reconcile.rule._cron_apply_rules()` (`@api.model`).
- `account.bank.statement.line._custom_applicable_rules()` / `_custom_apply_reconcile_rules()`.
- `sale.order._check_credit_limit()` / `_custom_credit_check()`.
- `custom.followup.level._cron_run_followup()` / `_cron_apply_followup()` (`@api.model`).
- `res.partner._custom_advance_followup_level()` / `_custom_send_followup_email_if_due()`.
- `account.move._custom_run_three_way_match()` / `_custom_compute_match()` / `_custom_get_match_policy()`.
- `res.company._sister_companies()` (`@api.model`).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `account`, `analytic`, `sale_management`, `purchase`, `mail`.
- **Inherits from:** `account.move` (+ `pdp.audited.mixin`), `account.bank.statement.line`, `res.partner`, `sale.order`, `res.company`, `account.analytic.account`, `account.chart.template` (PSAK template); `custom.fiscal.year`, `custom.consolidation.chart`, `custom.elimination.proposal`, `custom.credit.check.log`, `custom.match.result` mix `pdp.audited.mixin` + `mail.thread`.
- **Extended by:** `custom_accounting_asset`, `custom_accounting_recurring`, `custom_accounting_reports` (all declare it in `depends`).
- **External calls:** none.
- **Cross-vertical:** generic — every Indonesian SMB tenant needs all these features. Vertical modules should not redefine consolidation/intercompany/3-way/follow-up; extend here.

## Gotchas
- **Two parallel consolidation models.** `account.consolidation.config` is perimeter-based (companies × dates, ad-hoc TB). `custom.consolidation.chart` is group-COA-based (with persistent `chart.account` rows + per-company mappings + locked state). They share no data — BRD analysers must map "consolidated reporting" to the perimeter, "group-COA roll-up with FX" to the chart.
- **Intercompany mirror is best-effort.** Failure in `_custom_create_intercompany_mirror` is caught, posted to chatter, and the source move stays posted. Operators must reconcile manually. No retry queue.
- **Mirror lookup keys on `res.company.partner_id`.** A partner must be set as a company's partner for the rule to fire; this is brittle if partners are merged or companies are recreated.
- **3-way match runs BEFORE `super()._post`** — a `block` policy raises `UserError` and prevents posting. Match is also recomputed each post (`unlink` of old result), so audit history is lost on re-post.
- **Duplicate bill-ref block has no escape hatch.** The check in `account_move_bill_ref.py` is unconditional (no per-company toggle) — every tenant gets it because this module ships in all industry packs. If a vendor legitimately reuses a reference, the only ways through are changing the ref or cancelling the prior bill. Add a `res.company` boolean guard here if a tenant needs to opt out. Two `_post` overrides now exist on `account.move` in this module; both call `super()`, order follows `models/__init__.py` import order (bill-ref check registered after match).
- **`account.move.line.balance` is used in `_compute_balances`** — `read_group` of `debit:sum`/`credit:sum`, then `debit - credit`; this matches stored convention but does NOT honour FX revaluation moves separately.
- **Elimination amount uses `min(|a|,|b|)`** — the residual is left on whichever side is larger; no warning if the imbalance is material (only `threshold_amount` is exposed on the rule, but proposal.action_compute does not consult it).
- **`custom.followup.stat.by.partner` SQL view guards init** — checks `res_partner.custom_followup_level_id` exists before `CREATE VIEW`; on first install the view is empty until the second registry load.
- **PSAK template's `country_id` is `base.id`** — that XML id is the country Indonesia (`base.id` not "base record id"). Confusing string.
- **Credit limit uses `custom_credit_limit` only** — the native `account.credit_limit` field on `res.partner` is ignored. Imports must populate the custom field.
- **`x_custom_parent_id` on analytic accounts** is a manual hierarchy field — Odoo 19 dropped the native `parent_id`; do not assume native parent_path covers branches.
- **`_log_report_run` / `_pdp_audit_write`** insert directly into `pdp.audit_log` via raw SQL; failures are swallowed. Don't rely on audit being present in tests.

## Out of Scope
- **Asset depreciation** — see `custom_accounting_asset`.
- **Recurring journal entries / recurring payments** — see `custom_accounting_recurring`.
- **Financial reports (P&L, BS, GL, TB, Aging, Cash Flow, Tax)** — see `custom_accounting_reports`.
- **Indonesian withholding (PPh) and DPP Nilai Lain** — see `custom_tax_id`.
- **Bank statement file import (CSV/H2H)** — see `custom_bank_import`.
- **Payment gateway integration** — see `custom_payment_id`.
- **Approval workflow** — see `custom_approval_engine`.
- **Currency revaluation** — only `presentation_currency_id` is captured; no automatic FX reval.
- **Branch P&L / Branch BS reports** — the dimension is captured but not used in any report renderer.
- **Multi-tier intercompany graphs** — rules are pairwise (company A ↔ company B), no transitive resolution.
