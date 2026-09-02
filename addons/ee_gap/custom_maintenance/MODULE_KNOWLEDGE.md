---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_maintenance
manifest_version: 19.0.0.2.0
---

# custom_maintenance

## Purpose
Extends CE `maintenance` with **IoT-driven alerting, MTBF/MTTR analytics, predictive scheduling, team-SLA policies, spare-parts catalogue with stock-move materialisation, and cost tracking**. The module is the bridge between `custom_iot_bridge` sensor readings and the CE maintenance workflow: thresholds on `maintenance.equipment` are evaluated by a cron, breaches optionally auto-create `maintenance.request` corrective tickets, and an SLA cron escalates approaching/breached resolution deadlines via mail.

## Business Flow
- **Configuration**: maintenance admin sets `x_iot_threshold_metric` (e.g. `temperature_c`), `x_iot_threshold_value`, `x_iot_threshold_op` (gt/lt/eq), and `x_auto_request_on_breach` on a `maintenance.equipment`. Optionally defines `custom.maintenance.team.sla` per `(team_id, priority)` with `response_hours` + `resolve_hours`.
- **IoT breach cron** (`cron_check_iot_breaches`): scans equipment with thresholds, reads latest `iot.reading` newer than `x_last_iot_breach`, evaluates operator. On breach: stamps `x_last_iot_breach`, and if `x_auto_request_on_breach=True` creates a `maintenance.request` (`maintenance_type=corrective`, `priority=2`, populated description) with `x_iot_triggered=True` + `x_iot_metric_value`.
- **MTBF/MTTR compute** (`_compute_failure_stats`): on every change to maintenance_ids, walks done-stage corrective requests for the equipment. `x_total_failures` = count. `x_mttr_hours` = mean `(close_date - request_date)` in hours. `x_mtbf_hours` = `(last_failure - effective_date_or_first_failure) / failures`.
- **Predictive next maintenance** (`_compute_predicted_next_maintenance`): if `mtbf_hours > 0` and a base date exists, adds `int(mtbf_hours / 8.0)` days (treating 8h as an operating day). `x_predicted_via` = `mtbf` (preferred) or `iot` (fallback when only thresholds exist).
- **Schedule predicted maintenance**: `action_schedule_predicted_maintenance()` creates draft preventive `maintenance.request` per equipment with the prediction context in the description.
- **Spare parts on done**: when a request transitions to a done stage and has `x_spare_part_ids`, `_create_spare_part_stock_moves` creates `stock.move` rows from any internal location to production (or inventory) for each part — best-effort, silent if stock not installed.
- **Cost tracking**: `x_labor_cost` (manual) + `x_parts_cost` (sum of part `list_price`) → `x_total_cost`.
- **SLA**: `_compute_sla` resolves the best policy per `(team_id, priority)` with global fallback (team=False). `_compute_sla_deadlines` adds hours to `create_date`. `_compute_sla_status` returns ok/warn/breach/done. Cron `cron_check_sla_breach` posts a chatter note + emails team manager on first breach (idempotent via `x_sla_breach_notified`).
- **PDP audit** on equipment write: changes to `owner_user_id`, `employee_id`, or `department_id` write a raw SQL row into `pdp.audit_log` (classification `internal`).

## Key Models
- `maintenance.equipment` (inherited) — Adds IoT thresholds, MTBF/MTTR, predictive fields, PDP audit on owner changes.
- `maintenance.request` (inherited) — Adds IoT flags, spare parts (M2m product), SLA fields, cost fields, predictive stamps.
- `custom.maintenance.team.sla` — Per-team-per-priority SLA policy (response + resolve hours).

## Important Fields
- `maintenance.equipment.x_iot_threshold_metric` (Char) + `x_iot_threshold_value` (Float) + `x_iot_threshold_op` (gt/lt/eq) — breach definition.
- `maintenance.equipment.x_auto_request_on_breach` (Boolean) — gates auto-creation of corrective requests.
- `maintenance.equipment.x_last_iot_breach` (Datetime, readonly) — high-water mark for the cron to avoid duplicate triggers.
- `maintenance.equipment.x_total_failures` / `x_last_failure_at` / `x_mtbf_hours` / `x_mttr_hours` (computed/stored) — reliability metrics.
- `maintenance.equipment.x_predicted_next_maintenance` (Date, computed/stored) — `last_failure + mtbf_hours/8 days`.
- `maintenance.equipment.x_predicted_via` (mtbf/iot/manual).
- `maintenance.request.x_iot_triggered` (Boolean, readonly, tracking).
- `maintenance.request.x_priority_score` (Integer, computed/stored) — `priority*10 - 50 if done + 5 if iot_triggered`.
- `maintenance.request.x_spare_part_ids` (M2m product.product, domain `type='consu'`).
- `maintenance.request.x_sla_id` / `x_sla_response_deadline` / `x_sla_resolve_deadline` / `x_sla_status` (computed/stored).
- `maintenance.request.x_labor_cost` / `x_parts_cost` / `x_total_cost` (Monetary).
- `custom.maintenance.team.sla.team_id` (M2o, may be False for global default) + `priority` (0..3) + `response_hours` + `resolve_hours`.

## Public Methods
- `maintenance.equipment.cron_check_iot_breaches()` (`@api.model`) — Scan + create requests on breach.
- `maintenance.equipment.action_schedule_predicted_maintenance()` — Create draft preventive request from prediction.
- `maintenance.equipment._compute_failure_stats()` — MTBF/MTTR/total_failures.
- `maintenance.equipment._pdp_audit_owner_change(changes)` — Raw-SQL audit insert.
- `maintenance.request.cron_check_sla_breach()` (`@api.model`) — Recompute SLA status, post + email on new breaches.
- `maintenance.request._create_spare_part_stock_moves()` — Materialise stock.move per part on done.
- `custom.maintenance.team.sla._find_for(team_id, priority)` (`@api.model`) — Best-match policy with global fallback.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `maintenance`, `custom_iot_bridge`, `product`, `mail`.
- **Inherits from:** `maintenance.equipment` + `mail.thread`, `maintenance.request` + `mail.thread`.
- **Extended by:** vertical maintenance modules (mining, plant, fleet) may add domain metrics on equipment.
- **External calls:** queries `iot.reading` from `custom_iot_bridge`; sends `mail.mail` to team manager on SLA breach.
- **Cross-vertical:** generic IoT + reliability layer for any asset-heavy vertical.

## Gotchas
- **MTBF treats 8 hours = 1 operating day** when projecting next maintenance — hardcoded constant in `_compute_predicted_next_maintenance` (`days = int(mtbf_hours / 8.0)`). Continuous-operations equipment will be misjudged.
- **`x_parts_cost` sums `product.list_price`** (sale price), not `standard_price` — cost reporting will reflect price book, not COGS.
- **`cron_check_iot_breaches` requires `iot.reading` model** to exist; gracefully no-ops if `custom_iot_bridge` isn't installed despite being in `depends`.
- **`x_last_iot_breach` is updated even when auto-request is OFF** — so the cron won't re-trigger; effectively the operator must read the log.
- **PDP audit via raw SQL** (`env.cr.execute INSERT INTO pdp.audit_log`) — bypasses ORM; field/schema drift in `pdp.audit_log` will silently fail (caught by broad except).
- **`cron_check_sla_breach` only emails the FIRST team member** as "manager" — there is no manager role check; it uses `team.member_ids[:1]`.
- **SLA breach notification is per-request idempotent** via `x_sla_breach_notified` — once True it never re-fires, even on long-running breaches.
- **`_create_spare_part_stock_moves` posts `product_uom_qty=1.0`** per part regardless of actual quantity used; M2m has no qty column.
- **`_compute_sla` uses `priority or '2'`** — equipment with no priority falls into the Normal bucket silently.

## Out of Scope
- Inventory reservation for spare parts (just creates moves).
- Maintenance work-order routing / labour scheduling.
- Calendar-aware operating hours (8h flat constant).
- Predictive ML — predictions are statistical mean only.
- Cost roll-up to fixed-asset depreciation.
- Multi-team SLA escalation chains.
