---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_accounting_recurring
manifest_version: 19.0.0.1.0
---

# custom_accounting_recurring

## Purpose
Two scheduled-recurrence engines that close the CE-side gap against EE `account_accountant`'s recurring entries: (1) `custom.recurring.journal.template` produces balanced `account.move` entries on a monthly/quarterly/yearly cadence (lease accruals, prepaid amortisation, manual recurring postings); (2) `custom.recurring.payment.template` produces inbound/outbound `account.payment` records on the same cadence (standing-order vendor payments, recurring customer collections).

Both engines share the same period machinery (`relativedelta` map), the same `end_date` semantics, and the same `pdp.audited.mixin` audit trail.

## Business Flow
- Operator creates a `custom.recurring.journal.template` with `journal_id` (general), `period` ∈ monthly/quarterly/yearly, `next_date`, optional `end_date`, `auto_post`, and a balanced set of `custom.recurring.journal.template.line` rows (debit OR credit per line; `_check_balanced` enforces total_debit==total_credit; `_check_amounts` enforces a line cannot have both).
- `action_run_now()` (manual) or `_cron_generate_due()` (`@api.model`, daily) calls `_generate_one()` on every active template whose `next_date <= today`.
- `_generate_one()` creates an `account.move` (`move_type='entry'`, `custom_recurring_template_id=self.id`); if `auto_post`, posts immediately; advances `next_date` by `PERIOD_OFFSETS[period]` and stamps `last_generated_at`.
- Cron is resilient — exceptions are logged + `cr.rollback()` per template so one bad template doesn't break the batch.
- Payment template is parallel: `partner_id`, `payment_type` ∈ `inbound`/`outbound`, `journal_id` (bank/cash), `amount` (positive), and same period+next_date+end_date+auto_post+cron loop. `_generate_one()` creates `account.payment` with `partner_type` derived from `payment_type` (`customer` for inbound, `supplier` for outbound), optionally posts.
- Generated moves are surfaced on the template via `generated_move_ids` (`account.move.custom_recurring_template_id`).

## Key Models
- `custom.recurring.journal.template` — Header. Inherits `pdp.audited.mixin` + `mail.thread`. Code from sequence `custom.recurring.journal.template`.
- `custom.recurring.journal.template.line` — `account_id` + `partner_id` + `debit`/`credit` + `analytic_distribution` (Json, same shape as `account.move.line.analytic_distribution`).
- `custom.recurring.payment.template` — Header for payments. Inherits `pdp.audited.mixin` + `mail.thread`.
- `account.move` (inherited) — Adds back-ref `custom_recurring_template_id`.

## Important Fields
- `custom.recurring.journal.template.period` (Selection monthly/quarterly/yearly) — looked up in module-level `PERIOD_OFFSETS = {monthly: relativedelta(months=1), quarterly: relativedelta(months=3), yearly: relativedelta(years=1)}`.
- `custom.recurring.journal.template.next_date` (Date, required) — the *next* posting date; advances by period after each run.
- `custom.recurring.journal.template.end_date` (Date) — soft termination; cron skips when `next_date > end_date`.
- `custom.recurring.journal.template.auto_post` (Boolean, default True) — when False the move is left in draft.
- `custom.recurring.journal.template.code` (Char, readonly) — from `ir.sequence("custom.recurring.journal.template")`.
- `custom.recurring.journal.template.last_generated_at` (Datetime, readonly) — stamped by `_generate_one`.
- `custom.recurring.journal.template.line.debit` / `credit` (Monetary) — `_check_amounts` requires exactly one of the two to be set per line; `_check_balanced` requires total_debit == total_credit at the header level.
- `custom.recurring.journal.template.line.analytic_distribution` (Json) — `{analytic_account_id: percentage, ...}`, copied verbatim onto the generated `account.move.line`.
- `custom.recurring.payment.template.payment_type` (Selection inbound/outbound) — maps to `partner_type` automatically.
- `custom.recurring.payment.template.amount` (Monetary) — `_check_amount` requires > 0.
- `account.move.custom_recurring_template_id` (M2o, readonly, indexed) — back-pointer; `One2many` exposed as `generated_move_ids`.

## Public Methods
- `custom.recurring.journal.template.action_run_now()` / `_generate_one()`.
- `custom.recurring.journal.template._cron_generate_due()` (`@api.model`) — daily cron.
- `custom.recurring.payment.template.action_run_now()` / `_generate_one()`.
- `custom.recurring.payment.template._cron_generate_due()` (`@api.model`).
- `_compute_generated_count` (depends on `generated_move_ids`) — stat button counter.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_accounting_full`, `account`.
- **Inherits from:** `pdp.audited.mixin` + `mail.thread` on both templates; `account.move` extended with `custom_recurring_template_id`.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic.

## Gotchas
- **Payment template uses sequence `custom.recurring.journal.template`** — same sequence as journal templates (bug? intentional shared numbering? — inspect `data/ir_sequence_data.xml` before changing).
- **End-date is exclusive but inconsistently** — cron `_cron_generate_due` checks `tpl.next_date > tpl.end_date` to skip, BUT only after already including the template in the `next_date <= today` search; if `next_date == end_date`, the line WILL be generated.
- **`relativedelta(months=3)`** for quarterly is a calendar quarter, not a 90-day window — verify when matching periods to fiscal periods.
- **No catch-up for missed runs** — if cron was stopped for 3 months and `period=monthly`, only ONE entry is generated per `_generate_one` call (`next_date += 1 month`). A second cron run is required for each missed period.
- **`_check_balanced` allows empty `line_ids`** (skips when empty) — a template with no lines passes constraints but `_generate_one` raises `UserError("Template has no lines")` at runtime.
- **`auto_post=False` does not roll back `next_date`** — even if posting fails downstream, `next_date` has already been advanced; manual reposting must NOT call `action_run_now()` again.
- **No multi-company isolation in cron** — `_cron_generate_due` searches ALL companies; the per-template `company_id` is used only for the `with_company(...)` context on the generated move.

## Out of Scope
- **Auto-reverse / reversal entries** — once posted, a recurring move is just another `account.move`; reversal is manual.
- **Variable-amount recurrences** — `amount` (payment) and per-line `debit`/`credit` (journal) are fixed; no formula/escalation.
- **Pause/resume tooling** — `active=False` is the only off switch.
- **Per-line analytic plans** — `analytic_distribution` is freeform JSON; the constraint side does not validate keys exist.
- **Customer subscription recurring billing** — that's `custom_subscription` (separate module).
