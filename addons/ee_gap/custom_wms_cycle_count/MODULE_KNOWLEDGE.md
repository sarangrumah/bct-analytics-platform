---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_wms_cycle_count
manifest_version: 19.0.0.1.0
---

# custom_wms_cycle_count

## Purpose
Plan-driven perpetual-inventory / **cycle counting** with a session→line→adjustment workflow and a supervisor approval gate for variance posting. Replaces ad-hoc CE inventory adjustments with cadence-aware sampling (ABC velocity, random, by zone, by value, last-counted) plus a daily cron that materialises new counting sessions from due plans.

## Business Flow
- A warehouse manager creates a `custom.cycle.count.plan` per `stock.warehouse` with a `frequency` (daily/weekly/monthly/quarterly/adhoc), `method` (sampling strategy), optional `scope_zone_ids`, and a `target_count_per_period`.
- The daily cron `CycleCountPlan._cron_generate_sessions()` selects active plans where `next_run_date <= today` (excluding adhoc), invokes the `custom.cycle.count.start.wizard` to materialise a `custom.cycle.count.session`, and advances `next_run_date` per frequency delta (30/90 day flat approximations).
- The session starts in `draft` with auto-assigned `name` from `ir.sequence(custom.cycle.count.session)`; `action_start()` flips to `in_progress` and stamps `started_at`.
- For each `custom.cycle.count.line`, a counter calls `action_count(qty)` recording `counted_qty`, `counter_user_id`, `counted_at`; `variance_qty` / `variance_pct` are computed.
- Supervisor (group `custom_wms_cycle_count.group_cycle_count_supervisor`) calls `action_approve()` or `action_reject()` on each line. Approval with non-zero variance auto-creates a `custom.cycle.count.adjustment`.
- `action_post()` on the adjustment materialises a `stock.move` to/from `stock.location_inventory` (or any `usage=inventory` location fallback) to reconcile the variance.
- `action_review()` moves the session to `reviewing`; `action_close()` validates all lines are `approved` or `skipped` then closes (stamping `completed_at`).
- `is_new_item=True` lines + `new_item_product_temp_name` capture barcoded items that don't match any product (operator-recognition workflow).

## Key Models
- `custom.cycle.count.plan` — Cadence + scope definition; one per recurring count programme.
- `custom.cycle.count.session` — Materialised run instance; one per (plan, period); tracks `line_count`, `variance_count`, `variance_value`.
- `custom.cycle.count.line` — One (location, product[, lot]) tuple to count; status pending/counted/skipped/recount_required/approved/rejected.
- `custom.cycle.count.adjustment` — Variance-posting record; creates a `stock.move` against the inventory loss location.

## Important Fields
- `custom.cycle.count.plan.frequency` (Selection daily/weekly/monthly/quarterly/adhoc) — drives cron `_advance_next_run` (timedelta-based, month=30, quarter=90 days).
- `custom.cycle.count.plan.method` (Selection abc_velocity/random/by_zone/by_value/last_counted) — semantic tag for the start wizard's sampling logic.
- `custom.cycle.count.plan.target_count_per_period` (Integer, default 50) — used by `coverage_pct` compute.
- `custom.cycle.count.plan.next_run_date` (Date) — cron pivot.
- `custom.cycle.count.session.state` (draft/in_progress/reviewing/closed/canceled) — workflow gate; close requires all lines approved/skipped.
- `custom.cycle.count.session.variance_value` (Float, computed/stored) — `Σ |variance_qty| × product.standard_price`.
- `custom.cycle.count.line.variance_qty` / `variance_pct` (Float, computed/stored) — guarded against expected_qty=0.
- `custom.cycle.count.line.status` (Selection 6-state) — approval gate keyed on `approved`/`skipped`.
- `custom.cycle.count.line.is_new_item` + `new_item_product_temp_name` — captures unknown barcodes.
- `custom.cycle.count.adjustment.posted` (Boolean) — idempotency guard for `action_post`.

## Public Methods
- `custom.cycle.count.plan._cron_generate_sessions()` (`@api.model`) — daily cron entry; iterates due active plans, calls the start wizard, advances next_run_date.
- `custom.cycle.count.plan._advance_next_run()` — per-record cadence advance.
- `custom.cycle.count.session.action_start()` / `action_review()` / `action_close()` / `action_cancel()` — workflow transitions.
- `custom.cycle.count.line.action_count(qty)` — operator counting entry.
- `custom.cycle.count.line.action_approve()` / `action_reject()` / `action_recount()` — supervisor gate (group-checked).
- `custom.cycle.count.adjustment.action_post()` — creates the reconciling `stock.move`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_barcode`, `stock`, `product`, `mail`.
- **Inherits from:** `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin` (on plan/session/adjustment), `pdp.audited.mixin` only on line.
- **Extended by:** `custom_hht_bridge` (line counting from handheld terminal).
- **External calls:** none.
- **Cross-vertical:** generic WMS capability.

## Gotchas
- **Month / quarter advance is flat (30 / 90 days)** — `_advance_next_run` uses simple timedelta, not calendar-relativedelta.
- **`action_approve` requires `group_cycle_count_supervisor`** — raises `UserError` otherwise; ensure security data file is loaded.
- **`variance_value` uses `product.standard_price`** at compute time — historical cost is not captured; reprice on close will retroactively change session value.
- **`is_new_item` lines block close** unless explicitly set to `skipped`/`approved`.
- **Adjustment auto-finds inventory location** via `env.ref('stock.location_inventory')` falling back to any `usage='inventory'` — if neither exists, `action_post` raises `UserError`.
- **`action_post` must build a real inventory move, and three Odoo 19 details make that easy to get wrong** (all fixed 22-Jul-2026):
  1. **`stock.move.name` was removed.** Passing it raises `ValueError: Invalid field 'name' in 'stock.move'` — *every* cycle-count adjustment failed to post on 19.
  2. **`reference` is `compute=..., store=True` with no inverse**, so writing it is silently discarded. The label reaches it via **`inventory_name`**, which `_compute_reference` reads when `is_inventory` is set.
  3. **A created move stays in `draft`.** An earlier fix created the move and stopped there, so `posted` read True while on-hand was untouched — the variance was never reconciled. The move now mirrors `stock.quant._get_inventory_move_values` (`is_inventory`, `state='confirmed'`, `picked=True`, a ready `move_line_ids` entry) and is completed with `_action_done()`.

  `test_adjustment_post_actually_moves_stock` asserts the move reaches `done` **and** that on-hand equals the counted quantity — assert the ledger, not the `posted` flag.
- **No reservation handling** — counting an item with pending pickings doesn't unreserve them; variance posting can collide with outstanding moves.
- **Wizard `custom.cycle.count.start.wizard.action_start()` is referenced by the cron** but the sampling algorithm lives in the wizard module file (not shown here); ABC/random/by_zone/by_value/last_counted distinctions are implemented there.
- **`adhoc` frequency is excluded from the cron** — must be triggered manually via the start wizard.

## Out of Scope
- Cost-aware variance valuation (uses spot `standard_price`).
- Per-line photo evidence (only `remark`).
- Direct integration with `account.move` for valuation adjustments — only `stock.move` is created.
- Sampling algorithm definition (lives in the wizard).
- Multi-warehouse plans — one plan = one warehouse.
