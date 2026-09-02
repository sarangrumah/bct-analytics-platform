---
status: draft
generated_at: 2026-07-24T00:00:00Z
generator: claude-code-handwritten
module: custom_account_deferred
manifest_version: 19.0.1.0.0
---

# custom_account_deferred

## Purpose
Closes the Enterprise deferred-revenue/expense gap for Community: users set a start/end date on invoice/bill product lines, and on posting the module books a deferral entry to a configured deferred account plus monthly, day-count-prorated recognition entries back to P&L.

## Business Flow
1. User sets `deferred_start_date` / `deferred_end_date` on invoice/bill product lines.
2. On post, `account.move._post()` calls `_generate_deferred_entries()` for each invoice/receipt that doesn't already carry a `deferred_entry_type`.
3. Per deferrable line: one **deferral** move (`entry`, `deferred_entry_type='deferral'`) reclasses the full P&L balance to the company's deferred account and is posted immediately; then one **recognition** move per month-end (`_month_ends`), day-count prorated (rounding remainder absorbed by the last month). Recognition moves dated ≤ today post now; future ones stay draft with `auto_post='at_date'` and are posted by the core autopost cron.
4. Smart button `action_open_deferred_entries` on the origin lists the generated entries.
5. `button_draft()` on the origin resets and unlinks all generated entries (drafting posted ones first).

## Key Models
- `account.move` (`_inherit`) — generation + cleanup logic and links.
- `account.move.line` (`_inherit`) — deferred date fields + deferrability test.
- `res.company` (`_inherit`) — deferred account/journal config.
- `res.config.settings` (`_inherit`) — settings proxies.

No new `_name`, no `_auto=False` model.

## Important Fields
- `account.move`: `deferred_origin_move_id` (M2o self, readonly, `index="btree_not_null"`), `deferred_entry_type` (`deferral`/`recognition`), `deferred_generated_ids` (O2m self via origin), `deferred_generated_count` (Integer computed).
- `account.move.line`: `deferred_start_date`, `deferred_end_date` (Date, both `copy=False`).
- `res.company`: `deferred_expense_account_id` (domain `asset_current`/`asset_prepayments`), `deferred_revenue_account_id` (domain `liability_current`/`liability_non_current`), `deferred_journal_id` (journal type `general`); all `check_company=True`.
- `res.config.settings`: related read-write proxies for the three company fields.

## Public Methods
- `account.move._post(soft=True)` — triggers generation.
- `_generate_deferred_entries()` — idempotent generator (returns early if entries already exist).
- `_deferred_config()` — returns the configured accounts/journal.
- `action_open_deferred_entries()`, `button_draft()` (cleanup override), `_compute_deferred_generated_count()`.
- `account.move.line._is_deferrable()` (product line with income/expense account and both dates), `_check_deferred_dates()` (`@api.constrains`).
- Module-level helper `_month_ends(start, end)`.

## Integration Points
- **Depends on:** `account` only.
- **Extends:** `account.move._post`/`button_draft`, `account.move.line`, `res.company`, `res.config.settings`.
- Config via `res.config.settings`/`res.company` fields (NOT `ir.config_parameter`).
- Relies on the core `auto_post='at_date'` autopost cron — **no custom cron**.
- Menu `menu_custom_deferred_entries` → `action_custom_deferred_entries` (filtered `account.move` list).

## Gotchas
- Generation is idempotent: `if self.deferred_generated_ids: return` guards re-post cycles.
- Raises `UserError` if the deferred journal, or the matching expense/revenue account, is unconfigured.
- Deferred account is chosen by the line's `account_id.internal_group` (`expense` → expense account, else revenue account).
- Prorating: the last month absorbs the rounding remainder; zero-amount months and zero-balance lines are skipped.
- `button_draft` surfaces (does not bypass) lock-date errors when drafting/unlinking generated entries; unlinks with `force_delete=False`.
- Deferral moves are created with `skip_invoice_sync=True` and posted `soft=False`.
- Constraint: both dates or neither, and end ≥ start.

## Out of Scope
- No `_auto=False` schedule/report board (the report is a filtered `account.move` list, not a computed cube), no custom cron (leans on core autopost), no asset depreciation, only invoice/bill/receipt product lines with income/expense accounts, and no per-line partial reversal beyond full cleanup on origin reset.
