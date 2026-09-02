---
status: draft
generated_at: 2026-07-02T08:21:50Z
generator: bootstrap-v1
module: custom_wms_to_engine
manifest_version: 19.0.0.1.0
---

# custom_wms_to_engine

## Purpose
The `custom_wms_to_engine` module implements a rule-driven internal transfer orchestration system for warehouse management (WMS). It evaluates predefined rules based on triggers such as low water mark, expiry approaching, zone consolidation, and picking replenishment (plus a no-op `manual` trigger). The engine produces proposal dicts and materializes them into concrete transfer orders (`custom.transfer.order`) with backing `stock.move` internal transfers.

## Business Flow
1. **Rule Definition**: Admins define rules (`custom.to.rule`) with source/target location domain expressions (stored as text, evaluated via `safe_eval`) and trigger-specific parameters.
2. **Evaluation**: `custom.to.engine.evaluate_all` searches all active rules ordered by `priority asc, sequence asc` and dispatches each to a per-trigger `_eval_*` handler via `evaluate_rule`.
3. **Proposal Generation**: Each `_eval_*` handler returns proposal dicts (source/target location, product, lot, planned qty, reason).
4. **Materialization**: Proposals are turned into records. `materialize` creates the backing `stock.move`, will create the `custom.transfer.order` itself when none is passed, and performs the `draft`→`proposed` transition. The `cron_evaluate_and_materialize` entrypoint ties `evaluate_all` to TO creation.
5. **Execution**: Transfer orders are advanced manually via `action_start` (draft/proposed → in_progress) and `action_done`. There is no state-driven auto-execution; the cron only creates proposed TOs, it does not advance or execute them.

## Key Models
- **custom.to.engine** — `AbstractModel`; evaluates rules and materializes transfer orders.
  - Methods: `evaluate_all`, `evaluate_rule`, `_eval_*` (trigger evaluators), `materialize`, `cron_evaluate_and_materialize`
- **custom.transfer.order** — Concrete internal movement proposal/execution. Inherits `["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]`.
  - Fields: `name`, `rule_id`, `company_id`, `state`, `source_location_id`, `target_location_id`, `product_id`, `lot_id`, `planned_qty`, `actual_qty`, `picker_id`, `picked_at`, `dropped_at`, `stock_move_id`
- **custom.to.rule** — Rule definition. Inherits `["mail.thread", "pdp.audited.mixin"]`.
  - Fields: `name`, `sequence`, `active`, `company_id`, `warehouse_id`, `trigger`, `source_location_domain`, `target_location_domain`, `product_filter_json`, `low_water_qty`, `expiry_days_ahead`, `schedule_cron`, `priority`, `last_run_at`, `schedule_interval_minutes`
- **custom.transfer.order.manual.wizard** — `TransientModel` for ad-hoc TO creation.
  - Fields: `product_id`, `source_location_id`, `target_location_id`, `qty`, `deadline_at`, `priority`
  - Method: `action_create` — validates qty > 0 and source ≠ target, creates a `proposed` TO, and calls `engine.materialize`.

## Important Fields
- **custom.to.rule**
  - `name` (Char) — Rule name.
  - `trigger` (Selection: low_water_mark, expiry_approaching, zone_consolidation, picking_replenishment, manual) — Trigger type; default `manual`.
  - `source_location_domain`, `target_location_domain` (Char) — Odoo domain expressions (text) evaluated via `safe_eval`.
- **custom.transfer.order**
  - `name` (Char) — Order name (sequence-assigned from `custom.transfer.order`).
  - `state` (Selection: draft, proposed, in_progress, done, canceled) — State of the transfer order.
  - `source_location_id`, `target_location_id` (Many2one to stock.location) — Source and target locations.
  - `product_id` (Many2one to product.product) — Product being transferred.

## Public Methods
- **custom.to.engine**
  - `evaluate_all()` — Evaluates all active rules in priority/sequence order, returning aggregated proposal dicts.
  - `evaluate_rule(rule)` — Dispatches to `_eval_<trigger>`, returning that handler's proposals (or `[]`).
  - `_eval_low_water_mark`, `_eval_expiry_approaching`, `_eval_zone_consolidation`, `_eval_picking_replenishment`, `_eval_manual` — Per-trigger evaluators (`_eval_manual` returns `[]`).
  - `materialize(proposal_dict, transfer_order=None)` — Creates the backing `stock.move`; creates the TO when `transfer_order` is None; sets `stock_move_id`; transitions `draft`→`proposed`.
  - `cron_evaluate_and_materialize()` — Cron entrypoint: runs `evaluate_all` then creates a `proposed` TO per proposal.
- **custom.transfer.order**
  - `action_propose()` — Sets state to `proposed`.
  - `action_start()` — Requires state in (draft, proposed); sets `in_progress`, stamps `picker_id` and `picked_at`.
  - `action_done()` — Sets `done`, stamps `dropped_at`, defaults `actual_qty` to `planned_qty`.
  - `action_cancel()` — Sets `canceled`.
  - `action_materialize()` — Creates the backing `stock.move` via `engine.materialize` (skips if `stock_move_id` already set).

## Integration Points
- **Depends on** (manifest): custom_core, custom_pdp_audit, stock, product, barcodes, mail.
- **Inherits from**:
  - `stock.quant` (`write` override) — stamps low-water-mark rules dirty on quantity mutation.
  - `custom.to.rule` inherits `mail.thread`, `pdp.audited.mixin`.
  - `custom.transfer.order` inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
  - The `pdp.audited.mixin` inheritance is why `custom_pdp_audit` is a dependency.
- **External calls**: None.

## Gotchas
- The engine does not run evaluations inline. The `stock.quant.write` override stamps `last_run_at` on the low-water rules whose warehouse and company cover the mutated quants (`_to_rules_to_stamp`); a rule with no warehouse/company is treated as global and always in scope. **An earlier revision stamped *every* active low-water rule on *every* quantity write**, which made the marker useless (all rules always looked dirty) and wrote across the whole rule table on each stock move.
- Domain expressions for source/target locations are evaluated at runtime via `safe_eval`. A `_check_domains` constraint requires each to evaluate to a list; `_eval_domain` swallows eval errors and returns `[]` — so a malformed domain silently yields no proposals. **Odoo 19's `safe_eval` accepts at most 2 positional arguments**; the calls here pass `(raw, context)` and must stay that way (the sibling `custom_wms_putaway` shipped a 3-positional call that raised `TypeError` and silently disabled every domain-driven rule).
- `_eval_expiry_approaching` no-ops unless `stock.lot` has an `expiration_date` field. **`stock.location.scrap_location` was removed in Odoo 19** — searching it raised `KeyError` and took the whole rule evaluation down; the scrap destination now resolves as the company's first `usage='inventory'` location, matching `stock.scrap._compute_scrap_location_id`.
- `data/cron.xml` does bind `cron_evaluate_and_materialize` to a real daily `ir.cron` (anchored on the concrete `custom.transfer.order` model, since `custom.to.engine` is abstract). The cron only *creates* proposed TOs — it never advances or executes them.

## Out of Scope
- No external integrations. The module focuses on internal transfer order management within the stock/WMS context.
