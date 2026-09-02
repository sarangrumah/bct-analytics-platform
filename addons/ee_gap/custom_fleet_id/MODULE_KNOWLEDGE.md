---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_fleet_id
manifest_version: 19.0.0.2.0
---

# custom_fleet_id

## Purpose
Indonesia localization for the standard Odoo Fleet app. Adds STNK (Surat Tanda Nomor Kendaraan) and KIR (Kartu Uji Berkala) number/expiry tracking with computed status (`valid`/`expiring`/`expired`/`na`) and configurable alert windows, BBM (Pertamina fuel) type selection covering Pertalite/Pertamax/Pertamax Turbo/Dex/Dexlite/Solar/Listrik (EV), a fuel log with km/L consumption compute, full driver assignment history with single-active-per-vehicle constraint and PDP audit on driver change, next-service-due tracking by km or date, and an Indonesia plate-format validator (warning-only, non-blocking).

A daily cron posts STNK/KIR reminders to vehicle chatter and (when `maintenance` is installed) auto-creates a `maintenance.request` idempotently for renewals due within 30 days.

Note: **BPKB is mentioned in the task brief but is not implemented here** — only STNK + KIR are tracked.

## Business Flow
- An admin records `fleet.vehicle.x_stnk_number`, `x_stnk_expiry_date`, `x_stnk_alert_days_before` (default 30), and the equivalent KIR fields. Computed status is `expired` if delta<0, `expiring` if 0≤delta≤alert_days, else `valid`. KIR additionally has `na` for vehicles not subject to uji berkala.
- License plate input is validated against `^[A-Z]{1,2}\s\d{1,4}\s[A-Z]{1,3}$` (e.g. `B 1234 ABC`). Mismatches post a chatter note via `mail.mt_note`; only the context flag `custom_fleet_id_strict_plate` upgrades it to a hard `UserError` (used in tests).
- Driver assignment: writing `x_driver_partner_id` triggers `_pdp_audit_driver_change` (direct INSERT into `pdp.audit_log` with classification `internal`) and `_sync_driver_assignment_history` — closes any prior active assignment (`status='ended'` or `'transferred'`) and creates a new `custom.fleet.driver.assignment(status='active', start_date=today)`. A SQL-level constraint (`_check_single_active_per_vehicle`) prevents two active assignments per vehicle.
- BBM logging: an operator creates `custom.fleet.bbm.log(vehicle_id, date, odometer_km, liter, price_per_liter, gas_station, receipt_attachment)`. `_compute_consumption` derives km/L from delta-odometer vs liters since the previous log for that vehicle. `_sync_vehicle_odometer` pushes the highest reading back to `fleet.vehicle.x_current_odometer`.
- Service-due: stored compute `x_service_due` = `(current_odo ≥ next_service_km) OR (today ≥ next_service_date)`. Cron `cron_check_service_due` posts a chatter reminder for vehicles within 14 days or already due.
- STNK/KIR cron: `cron_check_expiry` runs daily; for vehicles in `expiring`/`expired` status posts a structured chatter note. If the standard `maintenance` module is installed and expiry falls within 30 days, `_create_stnk_kir_maintenance_request` creates a `maintenance.request(maintenance_type='preventive')` — idempotent per vehicle by matching on title `"STNK/KIR Renewal Needed: <plate>"` against requests whose `stage_id.done = False`.

## Key Models
- `fleet.vehicle` (inherited) — Adds 4 STNK/KIR fields each (+ status compute), BBM type, driver partner, service-due tracking, BBM log + driver-assignment O2M reverse links + counts, plate validator, write/create hooks, two daily crons.
- `custom.fleet.bbm.log` — Fuel log row with stored consumption compute; pushes odometer back to vehicle on save.
- `custom.fleet.driver.assignment` — Assignment history row; single-active per vehicle constraint; computed `duration_days`.

## Important Fields
- `fleet.vehicle.x_stnk_status` / `x_kir_status` (Selection, stored compute) — drives reminders + maintenance auto-creation.
- `fleet.vehicle.x_stnk_alert_days_before` / `x_kir_alert_days_before` (Integer, default 30) — slack window before expiry.
- `fleet.vehicle.x_bbm_type` (Selection: pertalite/pertamax/pertamax_turbo/dex/dexlite/solar/listrik) — Pertamina + EV catalog.
- `fleet.vehicle.x_driver_partner_id` (M2o `res.partner`, tracking) — current driver; write triggers PDP audit + history sync.
- `fleet.vehicle.x_current_odometer` (Float, km) — synced from BBM log highest reading.
- `fleet.vehicle.x_next_service_km` (Integer) / `x_next_service_date` (Date) / `x_service_due` (Boolean stored compute).
- `custom.fleet.bbm.log.odometer_km` (Integer, required, tracking) — feeds the vehicle's `x_current_odometer`.
- `custom.fleet.bbm.log.consumption_km_per_l` (Float, stored compute, digits=(8,2)) — km / liters since previous log.
- `custom.fleet.driver.assignment.status` (Selection: active/ended/transferred, tracking) — `active` is unique per vehicle.
- `custom.fleet.driver.assignment.duration_days` (Integer, stored compute) — `(end_date or today) - start_date`, floored at 0.

## Public Methods
- `fleet.vehicle.cron_check_expiry()` (`@api.model`) — STNK/KIR reminder + maintenance auto-create.
- `fleet.vehicle.cron_check_service_due()` (`@api.model`) — service-due chatter reminder.
- `fleet.vehicle._check_id_plate_format()` (`@api.constrains`) — Indonesia plate regex (warning unless `custom_fleet_id_strict_plate` in context).
- `fleet.vehicle._sync_driver_assignment_history(old_id, new_id)` — close previous active + create new active row.
- `fleet.vehicle._pdp_audit_driver_change(old_id, new_id)` — raw INSERT into `pdp.audit_log` (classification `internal`).
- `fleet.vehicle._maintenance_available()` — true if `maintenance` module installed.
- `fleet.vehicle._create_stnk_kir_maintenance_request(reason_lines)` — idempotent maintenance.request creator.
- `fleet.vehicle.action_open_bbm_logs()` / `action_open_driver_assignments()` / `action_add_bbm_log()` — smart-button helpers.
- `custom.fleet.bbm.log._sync_vehicle_odometer()` — push max odometer to the vehicle.
- `custom.fleet.driver.assignment._check_single_active_per_vehicle()` (`@api.constrains`).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `fleet`, `mail`.
- **Inherits from:** `fleet.vehicle`.
- **Extended by:** none declared.
- **External calls:** none — STNK/KIR / Samsat / Dishub integrations are out of scope.
- **Cross-vertical:** Indonesia-locked (plate format, fuel grades, STNK/KIR semantics).
- **maintenance:** soft dependency — auto-creation guarded by `_maintenance_available`.
- **pdp.audit_log:** raw SQL INSERT (not via the `pdp.audited.mixin` ORM path) — bypasses ORM hooks.

## Gotchas
- **PDP audit uses raw SQL INSERT** into `pdp.audit_log` rather than `pdp.audited.mixin._pdp_audit_write`. Failures are warning-logged and swallowed; ORM-level expectations (constraints, triggers) are bypassed.
- **Plate validator is non-blocking by default** — bad plates post a chatter note and `cron_check_expiry` still operates on them. Use `custom_fleet_id_strict_plate` context flag to enforce hard.
- **STNK/KIR maintenance request idempotency relies on title matching** — `"STNK/KIR Renewal Needed: <plate or display_name or id>"`. Plate/name changes between cron runs can break idempotency and create duplicates.
- **`maintenance_type='preventive'`** is conditionally set only if the field exists — some Odoo configs may reject the create without it.
- **`_compute_consumption` searches strictly previous logs by `odometer_km <`** — re-saving a log with a corrected lower reading flips ordering and recomputes downstream rows on next write only.
- **`_sync_vehicle_odometer` only advances, never decreases** `x_current_odometer` — odometer rollback (e.g. instrument replacement) is not modelled.
- **No BPKB tracking** despite the brief — only STNK/KIR are surfaced.
- **`x_kir_status` default is `na`** but the compute writes False (not `na`) when `x_kir_expiry_date` is missing inside the depends — verify default vs compute interaction for unset rows.
- **Single-active constraint is per-vehicle but enforced via `_check_single_active_per_vehicle` search**, not a partial unique index — race-prone under concurrent writes.

## Out of Scope
- Live Samsat / Dishub STNK/KIR validation API.
- BPKB (vehicle ownership) document tracking.
- Insurance / asuransi tracking + renewal.
- GPS / telematics ingestion (odometer is operator-entered or BBM-log-derived).
- Fuel-card integration (Pertamina MyPertamina, etc.).
- Multi-currency / multi-vehicle assignment per driver.
- Maintenance cost roll-up to accounting.
