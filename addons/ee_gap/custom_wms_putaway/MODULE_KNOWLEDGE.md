---
status: draft
generated_at: 2026-07-22T02:30:00Z
generator: claude-code-bootstrap-v1
module: custom_wms_putaway
manifest_version: 19.0.0.2.0
---

# custom_wms_putaway

## Purpose
Generic, configurable, tier-prioritised **putaway engine** that closes the CE-vs-EE gap for SAP-style ZWME001 multi-tier slotting. On every incoming `stock.move.line` create, the engine evaluates all active rules of all active strategies for the destination warehouse, scores them per kind, and either auto-rewrites `location_dest_id` (score >= the configured threshold) or surfaces a `custom.wms.putaway.suggestion` for operator review (typically through the HHT bridge).

Since 19.0.0.2.0 the engine **defers to the native Odoo 19 capacity model** rather than competing with it: `stock.package.type` supplies PxLxT and tare weight, `stock.storage.category` supplies the weight ceiling and per-package-type capacity, and this module only adds what the native model has no field for — bin geometry, walk order, and product-category reservation.

Rule kinds are pluggable: `fixed_location`, `nearest_empty`, `zone_round_robin`, `by_volume`, `by_dimension`, `by_weight`, `by_temperature`, `by_abc_velocity`, and a `safe_eval`-sandboxed `custom_python`.

## Business Flow
- Warehouse admin creates a `custom.wms.putaway.strategy` per `stock.warehouse`, picking a `rule_set` (default `zwme001_6tier`). A PostgreSQL `EXCLUDE` constraint allows only one active strategy per warehouse.
- Admin adds `custom.wms.putaway.rule` rows: each has `tier` (1..6, lower = higher priority), `sequence`, `kind`, optional `target_location_id` / `target_location_domain` / `product_categ_ids` / `product_domain`, optional `abc_class`, `temperature_zone`, `dock_location_id`, or a `custom_python` expression.
- An inbound `stock.picking` is processed; for every new `stock.move.line` on an `incoming` picking, `custom.putaway.engine.apply_top_proposal(move_line)` is invoked.
- `propose()` enumerates active rules in `(tier, sequence)` order. For each rule the candidate bins pass a **hard feasibility gate** (`_feasible_locations`) covering category reservation, weight ceiling and PxLxT fit, and only then are scored.
- A `custom.wms.putaway.suggestion` row is created. If the top score clears `custom_wms_putaway.auto_apply_threshold` (default 90), `action_apply()` runs immediately.
- Operator can `action_accept` / `action_reject`, or set `overridden_location_id` and `action_apply`.

## Key Models
- `custom.wms.putaway.strategy` — Per-warehouse rule container; exactly one active per warehouse.
- `custom.wms.putaway.rule` — Single tiered scoring entry; kind selects the handler.
- `custom.wms.putaway.suggestion` — Engine output awaiting operator decision.
- `custom.putaway.engine` (AbstractModel) — Feasibility + scoring + auto-apply service.
- `custom.wms.hd.pallet` — Handling unit / pallet tracker.
- `stock.location` (inherited) — capacity, geometry, walk order, category reservation.
- `stock.move.line` (inherited) — `create()` hook + category-reservation constraint.
- `product.template` / `product.product` (inherited) — `abc_class`, default handling unit.

## Important Fields
- `custom.wms.putaway.rule.kind` (Selection) — dispatches to `_score_<kind>`.
- `custom.wms.putaway.rule.product_categ_ids` (M2m) — declarative category filter; prefer it over the `product_domain` safe_eval string.
- `custom.wms.putaway.rule.dock_location_id` (M2o) — distance origin for `nearest_empty`.
- `custom.wms.putaway.rule.round_robin_cursor` (Integer, readonly) — rotation state for `zone_round_robin`.
- `stock.location.wms_length_mm` / `wms_width_mm` / `wms_height_mm` (Float) — bin opening.
- `stock.location.wms_max_weight_kg` (Float) — fallback ceiling; the storage category always wins.
- `stock.location.wms_walk_sequence` (Integer, indexed) — position along the physical route.
- `stock.location.wms_allowed_categ_ids` (M2m) + `wms_enforce_categ` (Boolean) — category reservation, advisory or hard.
- `product.template.wms_package_type_id` (M2o `stock.package.type`) — default handling unit; **optional**, the engine degrades to `product.volume` / `product.weight` without it.
- `product.template.wms_units_per_package` (Float) — units-to-handling-unit conversion.
- `ir.config_parameter` `custom_wms_putaway.auto_apply_threshold` (default 90).

## Public Methods
- `custom.putaway.engine.propose(move_line)` — Ranked proposals in `(tier, -score)` order.
- `custom.putaway.engine.apply_top_proposal(move_line)` — Creates the suggestion; auto-applies at/above the threshold.
- `custom.putaway.engine._auto_apply_threshold()` — Reads the config parameter, falling back to 90 on garbage.
- `custom.putaway.engine._feasible_locations(locations, move_line)` — The hard gate.
- `custom.putaway.engine._fits_dimensions(location, pkg_dims)` — PxLxT test, rotation-aware, permissive on unknown geometry.
- `custom.putaway.engine._rule_candidates(rule, move_line)` — Resolves domain-vs-pinned targeting, then gates.
- `custom.putaway.engine._native_capacity_free(location, product, move_line)` — Remaining units per `stock.storage.category`, or `None` when the native model has no opinion.
- `custom.putaway.engine._score_rule(rule, move_line)` — Dispatcher; **returns a 3-tuple** `(score, reason, location|None)`.
- `stock.location._wms_effective_max_weight()` / `_wms_free_weight_kg()` / `_wms_dims_mm()` / `_wms_accepts_category(categ)` / `_wms_walk_distance_to(other)`.
- `product.product._wms_package_type(move_line)` / `_wms_package_dims_mm(move_line)` / `_wms_gross_weight_kg(qty, move_line)` / `_wms_package_count(qty)`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_barcode`, `stock`, `product`.
- **Inherits from:** `stock.move.line`, `stock.location`, `product.template` + `product.product`, `mail.thread` + `pdp.audited.mixin`.
- **Extended by:** `custom_hht_bridge` (suggestion review at the HHT), `custom_wms_inbound_qc` (redefines `_is_incoming` so QC-pending receipts are not slotted and release transfers are), `custom_wms_docs` (walk-order picking documents).
- **External calls:** none.

## Gotchas
- **`safe_eval` in Odoo 19 takes `(expr, context)` positionally only.** The pre-19.0.0.2.0 code called `safe_eval(raw, {...}, {})`, which raised `TypeError` — swallowed by `_score_rule`'s defensive except. Every domain-driven rule therefore scored 0 and slotted nothing, silently, on every Odoo 19 database. Do not "restore" the old call shape.
- **`_score_rule` returns a 3-tuple.** Handlers that pick among candidates MUST return the chosen bin in slot 3; a 2-tuple means "use the rule's static target". Old 2-tuple callers break.
- **A pinned rule must not widen.** `_rule_candidates` treats `target_location_domain` as the narrower target; without it a rule holding only `target_location_id` would search every internal bin, because an empty domain still matches everything.
- **Dimensions are permissive by design.** `_fits_dimensions` returns True when either side has no geometry, so warehouses that never captured PxLxT keep the pre-dimension behaviour. Capturing geometry on *some* bins only will bias slotting toward the uncaptured ones.
- **`_sql_constraints` is silently ignored in Odoo 19** — the active-strategy uniqueness now uses `models.Constraint`. Verify with `select conname from pg_constraint where conrelid='custom_wms_putaway_strategy'::regclass`.
- **`_score_by_temperature` is still a stub** — there is no standard temperature field; it scores 75 whenever the rule names a zone and has a target.
- **`volume_used_m3` / `wms_weight_used_kg` are not stored** (`store=False`); they recompute on every read and can be slow on dense locations.
- **The auto-apply comparison is `>=`**, not `>`. Under the shipped default of 90 nothing changes (no handler returns exactly 90), but a tuned threshold now means "at or above".
- **`round_robin_cursor` advances on every scoring call**, including the one triggered by the auto-proposal hook inside `stock.move.line.create`. Tests that create a move line and score it in the same loop will see the cursor move twice per iteration.
- **`stock.location` has no posx/posy/posz in Odoo 19**; distance is `wms_walk_sequence` difference, falling back to common-prefix length of `complete_name`.

## Out of Scope
- Outbound/picking putaway — the hook is gated to receipts (and, via `custom_wms_inbound_qc`, QC release transfers).
- Slotting analytics / re-slotting cron — the engine is reactive on create only.
- Multi-location move-line splitting — proposals choose a single `location_id`.
- True 3D bin packing — `_fits_dimensions` tests one handling unit against the bin opening with horizontal rotation only; it does not stack or nest.
- Cross-warehouse putaway — strategies are bound to one `warehouse_id`.
