---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_accounting_asset
manifest_version: 19.0.0.2.0
---

# custom_accounting_asset

## Purpose
Fixed-asset register for Odoo CE — closes the EE `account_asset` gap. Maintains a per-company asset master file with hierarchical locations and groups (each with default useful life + default G/L accounts), generates straight-line or double-declining depreciation schedules, and runs a monthly cron that posts `DR depreciation_expense / CR accumulated_depreciation` journal entries for due lines. A disposal wizard captures sale value, computes gain/(loss) vs NBV, and books the retirement entry.

This is the canonical FA module. Anything BRD-related to "aset tetap", "penyusutan", "depreciation schedule", "disposal", "NBV" lives here.

## Business Flow
- Set up a `custom.fixed.asset.group` (default useful life, default asset/accum/expense accounts, default journal).
- Set up a `custom.fixed.asset.location` tree (`_parent_store=True`, recursive `complete_name`).
- Create a `custom.fixed.asset` in `draft`; `code` auto-assigned from `ir.sequence("custom.fixed.asset")`. `_onchange_group_id` copies group defaults into asset.
- `action_confirm()` requires expense + accumulated + journal accounts when `depreciation_method != "none"`; calls `_build_schedule()` then transitions `draft`→`running`. The schedule generator writes `custom.fixed.asset.depreciation.line` rows dated via `_depreciation_date_for(seq)` for `useful_life_months` periods. Straight-line uses `round(remaining/months_left, 2)` per month with rounding residual absorbed in the last line; declining uses `factor/total_months * NBV` with straight-line residual on the final period.
- **Depreciation dates are anchored on `posting_date`** (falls back to `acquisition_date` when empty) and shaped by `depreciation_date_mode`: `specific` (line 1 == posting date), `next_month` (default; line 1 == posting date + 1 month, the legacy behavior), `end_following_month` (last day of the month `seq` months after the anchor). `posting_date` defaults to `acquisition_date` on create.
- Monthly cron `_cron_post_due_depreciation` (calls `_post_due_depreciation()`): walks all `state='running'` assets, posts each unposted line whose `date <= today` as one `account.move` per line (DR expense / CR accumulated), flips `line.posted=True` and `line.move_id`.
- **Bulk manual posting**: the `custom.fixed.asset.post.wizard` (menu *Assets → Post Depreciation*) posts every running asset's due lines up to a chosen `cutoff_date` (optional group/location/company filters); the `action_post_due_depreciation_server` server action (list *Action* menu) calls `action_post_selected()` on the selected assets as of today. Both delegate to `_post_due_depreciation`.
- `action_open_dispose_wizard()` (running-only) opens `custom.fixed.asset.disposal.wizard`. The wizard computes `gain_loss = disposal_value - net_book_value` and, on `action_dispose()`, creates a balanced retirement move: DR accum + DR proceeds + DR loss / CR asset cost + CR gain. **Asset cost released = `acquisition_value + revaluation_value`** (full carrying). **If `revaluation_surplus_balance > 0` the move also transfers it DR revaluation surplus / CR retained earnings (IAS 16.41, equity-to-equity, not through P&L) and the balance is cleared.** Asset is written to `disposed` with `disposal_date`, `disposal_value`, `disposal_gain_loss`, `disposal_move_id`.
- **Revaluation** (running-only): `action_open_revaluation_wizard()` opens `custom.fixed.asset.revaluation.wizard`. The user enters a `new_value` (new carrying/NBV) and an optional revised `new_remaining_life`. `action_revalue()` books a balanced adjustment move **split per IAS 16** and tracks two running balances on the asset (`revaluation_surplus_balance`, `revaluation_loss_recognized`):
  - **Upward** (increment > 0): DR asset `increment`; the credit reverses any prior expensed decrease first — CR revaluation income `min(increment, loss_recognized)` — then CR revaluation surplus for the remainder.
  - **Downward** (increment < 0): CR asset `decrease`; the debit offsets any existing surplus first — DR revaluation surplus `min(decrease, surplus_balance)` — then DR revaluation loss for the remainder.
  It adds the increment to `revaluation_value`, optionally sets `useful_life_months = posted_count + new_remaining_life`, then calls `_build_schedule()` to re-spread the new remaining base over the remaining life. **Prospective: previously posted lines/moves are never touched.** A `custom.fixed.asset.revaluation` history record captures each event (amounts, account split, running balances after). Default surplus/loss/income/retained-earnings accounts come from the asset group (`custom.fixed.asset.group.default_revaluation_*`).
- `action_cancel()` allowed only if no depreciation has posted; `action_reset_draft()` unlinks all schedule lines and reverts to draft.
- Manual single-line posting via `custom.fixed.asset.depreciation.line.action_post_now()` (delegates to `_post_due_depreciation(as_of=line.date)`).

## Key Models
- `custom.fixed.asset` — asset master (acquisition + accounts + state machine + schedule O2m). Inherits `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin`.
- `custom.fixed.asset.group` — category w/ default useful life + default accounts + default journal.
- `custom.fixed.asset.location` — hierarchical (`_parent_store`) physical location; computed `complete_name`.
- `custom.fixed.asset.depreciation.line` — one row per scheduled period; `posted` + `move_id` set when GL booked.
- `custom.fixed.asset.revaluation` — persistent history record of each revaluation (date, NBV before, new value, adjustment, life before/after, accounts, `move_id`); O2m `revaluation_ids` on the asset.
- `custom.fixed.asset.disposal.wizard` (TransientModel) — captures disposal_date + disposal_value + gain/loss accounts.
- `custom.fixed.asset.revaluation.wizard` (TransientModel) — captures new_value + optional new_remaining_life + surplus/loss/journal; books the adjustment move and rebuilds the schedule tail.
- `custom.fixed.asset.post.wizard` (TransientModel) — bulk-posts due depreciation up to `cutoff_date` with optional group/location/company filters.

## Important Fields
- `custom.fixed.asset.state` (Selection draft/running/disposed/cancelled) — only `running` is depreciated; `disposed` is terminal.
- `custom.fixed.asset.code` (Char, unique per company via `code_company_unique`) — auto from sequence.
- `custom.fixed.asset.acquisition_value` / `salvage_value` (Monetary) — `_check_salvage` bans `salvage > acquisition` and negatives.
- `custom.fixed.asset.useful_life_months` (Integer, default 60) — must be ≥1 when method ≠ none. Revaluation may rewrite this to `posted_count + new_remaining_life`.
- `custom.fixed.asset.posting_date` (Date) — depreciation schedule anchor; falls back to `acquisition_date` when empty.
- `custom.fixed.asset.depreciation_date_mode` (Selection specific/next_month/end_following_month, default next_month) — how each line date is derived from `posting_date`.
- `custom.fixed.asset.revaluation_value` (Monetary, readonly, default 0) — net cumulative revaluation booked to the asset account; folded into `_depreciable_base` and `net_book_value`.
- `custom.fixed.asset.revaluation_surplus_balance` (Monetary, readonly, default 0) — equity surplus held for this asset; offset by downward revaluations and transferred to retained earnings on disposal.
- `custom.fixed.asset.revaluation_loss_recognized` (Monetary, readonly, default 0) — cumulative expensed decrease reversed (as income) by a later upward revaluation before crediting surplus.
- `custom.fixed.asset.group.default_revaluation_surplus_account_id` / `default_revaluation_loss_account_id` / `default_revaluation_income_account_id` / `default_retained_earnings_account_id` — revaluation account defaults pulled into the revaluation/disposal wizards.
- `custom.fixed.asset.depreciation_method` (Selection straight_line/declining/none) — `none` skips schedule entirely.
- `custom.fixed.asset.declining_factor` (Float, default 2.0) — factor for double-declining (2.0 = DDB).
- `custom.fixed.asset.asset_account_id` / `depreciation_account_id` / `expense_account_id` (M2o `account.account`) — overrides group defaults.
- `custom.fixed.asset.journal_id` (M2o `account.journal`, type=general) — depreciation journal.
- `custom.fixed.asset.accumulated_depreciation` / `net_book_value` (Monetary, computed, non-stored) — `sum(posted lines)` and `acquisition + revaluation_value - accum`.
- `custom.fixed.asset.disposal_date` / `disposal_value` / `disposal_gain_loss` / `disposal_move_id` (readonly, set by wizard).
- `custom.fixed.asset.depreciation.line.posted` (Boolean) — gates the cron; once True the line is immutable to the cron.
- `custom.fixed.asset.depreciation.line.sequence` (Integer, required) — drives schedule order; new lines built from `max(sequence)+1`.

## Public Methods
- `custom.fixed.asset.action_confirm()` — validates accounts → builds schedule → state=running.
- `custom.fixed.asset.action_cancel()` / `action_reset_draft()` — both refuse if any line is posted.
- `custom.fixed.asset.action_open_dispose_wizard()` / `action_open_revaluation_wizard()` / `action_view_revaluations()` — running-only (last two).
- `custom.fixed.asset._build_schedule()` — preserves posted lines, rebuilds unposted from current parameters.
- `custom.fixed.asset._depreciation_date_for(seq)` — anchor + mode → line date.
- `custom.fixed.asset._depreciable_base()` — `max(0, acquisition + revaluation_value - salvage)`.
- `custom.fixed.asset._post_due_depreciation(as_of=None)` — posts due unposted lines; one `account.move` per line.
- `custom.fixed.asset._cron_post_due_depreciation()` (`@api.model`) — monthly cron entry.
- `custom.fixed.asset.action_post_selected()` — bulk-post due lines (as of today) for `self`; wired to the list server action.
- `custom.fixed.asset.depreciation.line.action_post_now()` — manual single-line posting.
- `custom.fixed.asset.disposal.wizard.action_dispose()` / `_create_disposal_move()` — builds balanced retirement move (releases `acquisition + revaluation_value`).
- `custom.fixed.asset.revaluation.wizard.action_revalue()` / `_create_revaluation_move()` — books adjustment + rebuilds schedule tail (prospective).
- `custom.fixed.asset.post.wizard.action_post()` — bulk posting up to `cutoff_date`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_accounting_full`, `account`.
- **Inherits from:** `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin` on `custom.fixed.asset`; `mail.thread` on disposal wizard not used.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic.

## Gotchas
- **One `account.move` per depreciation line** — high-volume installations (thousands of assets × monthly) will create a lot of moves. No batching.
- **Schedule preservation is partial** — `_build_schedule` keeps `posted=True` lines but `unlinks all unposted`; if you change `useful_life_months` mid-life, unposted lines are rebuilt with `months_left = months - len(posted lines)`, NOT recalculated from the new total — verify before relying on parameter changes.
- **Declining method falls back to straight-line in the final period** to consume the full base — the last line absorbs the rounding residual; gain/loss arithmetic at disposal depends on this.
- **Rounding fudge** in straight-line: monthly = `round(remaining/months_left, 2)`, final line = `round(remaining - running, 2)`. Total matches `_depreciable_base()` but per-period values do not sum exactly to `monthly * months`.
- **`salvage_value` is captured but NOT booked at disposal** — disposal arithmetic uses NBV (`acquisition - accumulated`), not salvage. If posted depreciation never reaches `base - salvage` (e.g. early disposal), the residual asset cost stays on books via the disposal move's `CR asset_cost = acquisition_value` line.
- **No multi-currency depreciation** — `currency_id` is related from `company_id`; assets in a non-company currency lose precision.
- **`action_cancel` after posted lines is permanently blocked** — must reverse the posted moves manually before retry.
- **Group defaults only apply via `_onchange_group_id`** — record-level write of `group_id` from code does NOT cascade defaults; the UI onchange is the only writer.
- **Revaluation is prospective, net-carrying-value method** — the adjustment is booked against the asset account (`revaluation_value`); posted depreciation is never restated. Because it feeds `_build_schedule` via `revaluation_value`, `remaining` resolves to `new_value - salvage` re-spread over the remaining life.
- **IAS 16 equity/P&L split is automated** — a downward revaluation debits `revaluation_surplus_balance` before P&L; an upward revaluation credits P&L income up to `revaluation_loss_recognized` before surplus; disposal transfers the residual surplus to retained earnings. The split relies on the two running-balance fields — do NOT edit them by hand or the next revaluation/disposal will mis-split. Impairment testing is still out of scope (revaluation is user-triggered, not fair-value-driven).

## Out of Scope
- **Impairment** — no separate impairment test/workflow (revaluation is manual, user-entered).
- **Componentisation** — one asset = one schedule; no parent/child component depreciation.
- **Asset transfer between companies** — disposal wizard only handles sale/write-off.
- **Tax depreciation parallel ledger** — single schedule only; no tax vs accounting book split.
- **Asset tagging / barcoding** — `code` is human; no `barcode` field. See `custom_hht_bridge` for scanning workflows.
