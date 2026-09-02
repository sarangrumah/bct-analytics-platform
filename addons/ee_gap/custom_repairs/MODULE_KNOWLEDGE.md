---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_repairs
manifest_version: 19.0.1.0.0
---

# custom_repairs

## Purpose
Extends CE `repair.order` for **internal asset maintenance** (repairs on the
company's own equipment, not external-customer jobs). Links each repair to a
`maintenance.equipment` asset and bridges to the maintenance module by
auto-creating a corrective `maintenance.request`, feeding the asset's
maintenance history (MTBF/MTTR in `custom_maintenance`). Adds turnaround SLA,
labour + material cost analysis, optional MRP work-order link, optional
quality check on completion, and a re-open/rework flag. All extra fields are
namespaced `x_*` so the module composes cleanly with other repair extensions.

> Reoriented in 19.0.1.0.0 from the earlier customer-facing form (product
> warranty matrix, WhatsApp-to-customer status, customer complaint/returns).
> Those were removed; the WhatsApp action and `custom.repairs.warranty.matrix`
> model are gone. See `migrations/19.0.1.0.0/pre-migrate.py`.

## Business Flow
- Operator links the repair to an internal asset via `x_equipment_id`
  (`maintenance.equipment`) and records the internal fault in `x_id_complaint`
  and the requester in `x_requesting_user_id` / `x_requesting_team_id`.
- On `write({'state':'confirmed'})`, two best-effort bridges fire:
  - `_maybe_create_maintenance_request` opens a corrective
    `maintenance.request` on the linked asset (idempotent, `.sudo()`, no-op
    when no equipment or `maintenance` absent). Stored on
    `x_maintenance_request_id`; the asset's `maintenance_ids` back-link
    populates automatically.
  - `_maybe_create_mrp_workorder` creates an `mrp.production` stub when
    material lines exist and `mrp` is installed. Stored on `x_mrp_production_id`.
- Operator sets `x_promised_completion_date`; `_compute_sla_status` returns
  on_track / at_risk (≤ 1 day) / breached / done.
- On `write({'state':'done'})`, `x_actual_completion_date` is auto-stamped,
  then `_maybe_launch_quality_check` best-effort creates a `quality.check`
  against the first matching `quality.point` (by `product_id` when present,
  else any).
- Cost compute (`_compute_total_repair_cost`): material cost iterates
  `move_ids` (Odoo 19) / `operations` / `parts_lines` (older), preferring
  `product.standard_price * qty`, falling back to `price_subtotal` /
  `price_total` / `price_unit`. Labour cost = `x_labor_hours * x_labor_rate`
  (default rate from ICP `custom_repairs.labor_rate`, default 100 000 IDR/hour).
- `action_set_rework()` flags `x_returned=True` + stamps `x_return_date`;
  chatter post ("Repair re-opened for rework.").

## Key Models
- `repair.order` (inherited) — Adds `x_*` fields covering asset link / SLA /
  cost / rework / MRP / quality.

## Important Fields
- `repair.order.x_equipment_id` (M2o `maintenance.equipment`, tracking) — the internal asset.
- `repair.order.x_maintenance_request_id` (M2o `maintenance.request`, readonly) — bridged corrective request.
- `repair.order.x_requesting_user_id` (M2o `res.users`, default=current user, tracking).
- `repair.order.x_requesting_team_id` (M2o `maintenance.team`).
- `repair.order.x_promised_completion_date` (Date, tracking).
- `repair.order.x_actual_completion_date` (Datetime, readonly) — auto-stamped on done.
- `repair.order.x_sla_status` (on_track/at_risk/breached/done, computed/stored).
- `repair.order.x_id_complaint` (Text) — internal fault description.
- `repair.order.x_labor_hours` / `x_labor_rate` / `x_material_cost` / `x_labor_cost` / `x_total_repair_cost`.
- `repair.order.x_returned` (Boolean, readonly, copy=False) "Re-opened / Rework" + `x_return_date` (Datetime, readonly) + `x_return_reason` (Text).
- `repair.order.x_mrp_production_id` (M2o `mrp.production`, readonly).
- `repair.order.x_quality_check_ids` (O2m `quality.check`, computed) + `x_quality_check_count`.

## Public Methods
- `repair.order.action_set_rework()` — Mark re-opened for rework + stamp date + chatter.
- `repair.order.action_view_maintenance_request()` — Open the linked maintenance request.
- `repair.order._maybe_create_maintenance_request()` — Best-effort corrective `maintenance.request` on confirm (idempotent).
- `repair.order._maybe_create_mrp_workorder()` — Best-effort `mrp.production` stub on confirm.
- `repair.order._maybe_launch_quality_check()` — Best-effort `quality.check` on done.
- `repair.order._material_line_records()` — Probe move_ids/operations/parts_lines for version-portable line access.
- `repair.order._material_cost_field_candidates()` — Field-name fallback chain for material cost.
- `repair.order._default_labor_rate()` (`@api.model`) — Read `custom_repairs.labor_rate` ir.config_parameter (default 100 000 IDR).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_quality_full`, `repair`, `maintenance`, `mail`.
- **Inherits from:** `repair.order`.
- **External calls:** Creates `maintenance.request` on the linked equipment; optional `mrp.production` + `quality.check` records when those modules are installed.

## Gotchas
- **Bridge is idempotent** — guarded by `x_maintenance_request_id`; re-confirm after cancel won't duplicate the request.
- **Bridge no-ops without an asset** — `x_equipment_id` empty (or `maintenance` uninstalled) → no request created.
- **`maintenance.request` create is `.sudo()` + try/except** — a repair user lacking maintenance rights, or an unresolvable default team, is logged at INFO and skipped, not raised.
- **`maintenance_team_id` is not passed** — relies on the request's own default / equipment-derived team. If the DB has no team and the equipment has none, create can fail (caught).
- **`_maybe_create_mrp_workorder` creates an `mrp.production` with NO BoM** — just product + qty=1 + origin. Placeholder, not a real routing.
- **`_maybe_launch_quality_check` matches `quality.point` by `product_id`** then falls back to *any* point — can launch unrelated checks if no product-specific point exists.
- **`x_quality_check_ids` compute matches by `name like rec.name`** — non-relational, brittle if check names don't contain the repair name.
- **Repurposed columns kept for data continuity** — `x_id_complaint`, `x_returned`, `x_return_date`, `x_return_reason` kept their DB columns; only labels changed. Obsolete warranty/notification columns dropped in the 19.0.1.0.0 pre-migration.
- **Default labour rate is 100 000 IDR/hour** (Indonesian assumption); override via `ir.config_parameter('custom_repairs.labor_rate')`.

## Out of Scope
- Preventive/scheduled maintenance planning (lives in `custom_maintenance` on `maintenance.equipment`).
- Repair routing with multi-station BoM.
- Photo evidence of damage / repair.
- Multi-currency (cost fields are Float, not Monetary).
- External-customer messaging / portal (removed — this is an internal workflow).
