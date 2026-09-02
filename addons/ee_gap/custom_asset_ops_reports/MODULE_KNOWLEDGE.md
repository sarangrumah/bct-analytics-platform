---
status: draft
generated_at: 2026-07-16T00:00:00Z
generator: hand-authored
module: custom_asset_ops_reports
manifest_version: 19.0.0.1.0
---

# custom_asset_ops_reports

## Purpose
Five operational reports for the Inventory / warehouse team covering the
drone fleet: asset opname, per-event movement, spare parts, maintenance health
and repair history. They answer the tenant half of the tenant report-requirements
sheet (items 15–19); the finance half is served by `custom_accounting_reports`.

This module contributes **reports only** — it defines no business data. Every
model is an AbstractModel over data owned by other modules.

## Business Flow
1. A user opens *Operational Reports → <report>* and fills the wizard (period
   and/or company; the opname report also filters by asset group / location /
   state).
2. **View** opens the shared OWL table client action; **Export Excel** streams an
   XLSX. There is deliberately no PDF (see Gotchas).
3. Both paths run the same `_xlsx_columns()` + `_build_lines(filters)` contract
   on the report model, exactly like every report in `custom_accounting_reports`.

## Key Models
All are AbstractModels with `_inherit = "custom.report.engine"`:

- `custom.report.asset.opname` (`asset_opname`) — #15. One row per
  `custom.fixed.asset` (the drone register), enriched with an operational
  state from `rental.asset` and a condition from the latest `custom.bast.line`.
  Snapshot: it ignores the period.
- `custom.report.event.movement` (`event_movement`) — #16. One row per
  `stock.move` on a rental order's pickup (OUT) / return (IN) picking, grouped by
  event with a per-event quantity subtotal. `stock.move.is_loan` separates
  loan/cadangan tools from rented units.
- `custom.report.spareparts` (`spareparts`) — #17. One row per spare-part
  product, pairing a `stock.quant` availability snapshot with in-period usage.
- `custom.report.maintenance.health` (`maintenance_health`) — #18. Aggregates
  `maintenance.request` per equipment and **reads** the reliability metrics
  already computed on `maintenance.equipment` (MTBF / MTTR / failures).
- `custom.report.repair.history` (`repair_history`) — #19. One row per
  `repair.order` created in the period, with SLA, rework flag and costs.

Each has a matching `custom.report.*.wizard` TransientModel inheriting
`custom.report.wizard.mixin`.

## Important Fields
This module defines no stored fields. The fields it *reads* and that constrain
the reports:

- `custom.fixed.asset.serial_number` — added by the **tenant** module
  `custom_asset_register_seed`, not by the generic asset app; it is the only
  join key back to rental/BAST records. This module does **not** depend on that
  tenant module, so the field may be absent — `_has_serial()` guards every read
  and the enrichment degrades to blank.
- `stock.move.is_loan` — added by `custom_rental`; drives the Rental vs Tool/Loan
  column.
- `maintenance.request.x_spare_part_ids` — many2many with **no per-part
  quantity**, hence "Used" is a count of requests, not a consumed qty.
- `maintenance.equipment.x_mtbf_hours` / `x_mttr_hours` / `x_total_failures` /
  `x_last_failure_at` — read, never recomputed here.
- `repair.order.x_sla_status` / `x_returned` / `x_labor_cost` /
  `x_material_cost` / `x_total_repair_cost` — all from `custom_repairs`.

## Public Methods
Per report model: `_xlsx_columns()` and `_build_lines(filters)` — the engine
contract. Per wizard: `action_view()` (from the mixin) and
`action_export_xlsx()`. No `action_print`.

## Integration Points
- **Depends on:** custom_accounting_reports (the engine + wizard mixin),
  custom_accounting_asset, custom_rental, custom_maintenance, custom_repairs,
  custom_bast, stock. The list is deliberately wide so the module can only
  install where the fleet is actually operated in Odoo — a report over apps
  nobody uses would just render empty.
- **Deliberately does NOT depend on `custom_asset_register_seed`.** That is a
  `_tenants/` module, and a generic `ee_gap` module depending on a tenant one
  inverts the layering. Concretely it also drags that module's
  `post_init_hook`, which seeds 3,329 the drone assets — installing these
  reports on a training DB once meant seeding thousands of assets with no
  matching begbal GL accounts. The only thing wanted from it was
  `serial_number`, which is now read defensively.
- **Registers into:** `REPORT_MODEL_MAP` in
  `custom_accounting_reports/models/custom_report_dispatch.py`, via
  `setdefault` calls in `models/__init__.py`. That map is what lets the OWL table
  client action and the XLSX exporter resolve a report code.
- **Security:** reuses `custom_accounting_reports.group_report_user`; the root
  menu is gated on it.

## Gotchas
- **Screen + Excel only, no PDF.** These are working lists (and #15 runs to
  thousands of rows), so they are not registered in the QWeb router in
  `reports/report_common.xml`. Only codes needing a PDF go there — the branch P&L
  is the precedent.
- **#15's enrichment is best-effort, matched on the serial string.** There is no
  FK from the accounting asset register to `rental.asset` or to BAST lines, so
  operational state and condition are looked up by serial and come back blank
  when nothing matches. Do not read a blank condition as "good".
- **The two `x_sla_status` fields are not the same selection.**
  `maintenance.request` uses `ok/warn/breach/done`; `repair.order` uses
  `on_track/at_risk/breached/done`. Never share a label map between #18 and #19.
- **#17's "Used" is a request count, not a quantity**, because
  `x_spare_part_ids` carries no qty. `x_parts_cost` is a per-request total and is
  therefore not attributed per part.
- **#19 filters on `create_date`**, since `repair.order` has no reliable
  business date of its own on CE.
- Wizard many2many relation names must be set explicitly when the model name is
  long: the auto-generated
  `custom_fixed_asset_location_custom_report_asset_opname_wizard_rel` is 65
  chars and PostgreSQL truncates identifiers at 63, which fails the install.
- Adding a TransientModel here is cheap (this module is tenant-scoped by its
  dependencies), unlike `custom_accounting_reports`, which ships everywhere and
  forces an `-u` on every database that has it.

## Out of Scope
- No data model of its own: conditions, movements, parts and repairs are all
  owned by `custom_bast`, `custom_rental`, `custom_maintenance`, `custom_repairs`.
- No stock valuation or costing — quantities and the costs already computed
  upstream only.
- #16 reports rental pickings only; ad-hoc warehouse transfers unrelated to a
  `rental.order` are not "events" and do not appear.
