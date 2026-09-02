---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_attendance
manifest_version: 19.0.0.2.0
---

# custom_attendance

## Purpose
Extends CE `hr_attendance` with geofenced GPS check-in/out (haversine validation), a PIN-based public kiosk portal, an anomaly-driven manual approval workflow (long shifts > 12h or late-night check-ins), automatic overtime hours computation (configurable threshold + weekday/weekend rules), one-click conversion of OT hours into `hr.work.entry` records that feed `custom_hr_payroll_id`, and a face-recognition verification stub bridged to `custom.ai`.

## Business Flow
- HR seeds `attendance.geofence` records (name, latitude/longitude, `radius_meters`, company_id).
- HR seeds `custom.attendance.overtime.rule` rows (one per `differential` ∈ weekday/weekend/holiday) via `data/attendance_overtime_rule_seed.xml`; each has `threshold_hours` (default 8.0), `multiplier` (default 1.5), `is_active`.
- Employee opens `/custom_attendance/kiosk` on a tablet (auth=public). A cookie `custom_attendance_kiosk` carrying an opaque token is set on first GET.
- Employee enters their `hr.employee.pin` (4+ digits) and submits to `/custom_attendance/kiosk/submit` with optional lat/lng. `_kiosk_resolve_employee_by_pin` looks up the employee; `_kiosk_toggle` either:
  - If an open attendance (no `check_out`) exists → write `check_out=now`, `x_check_out_lat/lng` if provided.
  - Else → create a new `hr.attendance` with `check_in=now`, `x_check_in_lat/lng`, and `x_kiosk_session=<cookie token>`.
- `_compute_geofence_validated` runs haversine between `(x_check_in_lat, x_check_in_lng)` and `(x_geofence_id.latitude, x_geofence_id.longitude)`; valid if distance ≤ `radius_meters`.
- `_compute_overtime_hours` reads `worked_hours` and the best-matching active rule (weekday/weekend via `check_in.weekday() >= 5`) and stores `x_overtime_hours = max(0, worked_hours - threshold_hours)` (fallback threshold 8.0 if no rule).
- `_compute_approval_required` flags `x_approval_required=True` when `worked_hours > 12.0` OR `check_in.hour >= 22 or < 5`.
- Anomalous attendances: `action_request_approval()` (draft/rejected → pending) schedules a `mail.activity` for the employee's manager (via `parent_id.user_id`, fallback to current user). Manager calls `action_approve()` or `action_reject()` (pending → approved/rejected, stamps `x_approval_by`).
- Approved attendance with OT: `action_create_overtime_work_entry()` ensures an `hr.work.entry.type` with `code='OT'` exists (creates one if missing), cancels the previous work entry if re-run (idempotent), creates a new `hr.work.entry` (`state='draft'`, duration = `x_overtime_hours`, `date_start=check_in`, `date_stop=check_in + OT hours`), and writes `x_payroll_work_entry_id` + `x_payroll_synced=True`. On `unlink`, linked work entries are cancelled.
- Optional face verification: `action_verify_face()` calls `custom.ai._recommend(model='hr.attendance', res_id=self.id, payload={...})`; parses confidence (0-1) from response, sets `x_face_recognition_confidence`. If `confidence < 0.6`, forces `x_approval_required=True` and posts chatter.

## Key Models
- `hr.attendance` (inherited) — Adds GPS, kiosk session, approval workflow, OT, payroll bridge, face-recognition fields; mixes in `mail.thread`, `mail.activity.mixin`.
- `attendance.geofence` — Geofence definition (lat/lng + radius_meters, default 100).
- `custom.attendance.overtime.rule` — Rule rows; `(threshold_hours >= 0)` and `(multiplier > 0)` CHECK constraints.

## Important Fields
- `hr.attendance.x_geofence_id` (M2o `attendance.geofence`) — assigned fence; without it, validation is False.
- `hr.attendance.x_geofence_validated` (Boolean, computed, stored) — depends on geofence + check_in coords; **only checks check-in**, not check-out.
- `hr.attendance.x_check_in_lat/lng` / `x_check_out_lat/lng` (Float, digits=(10,7)).
- `hr.attendance.x_overtime_hours` (Float, computed, stored) — depends on `worked_hours` and `check_in` (for weekday/weekend rule lookup).
- `hr.attendance.x_approval_state` (Selection: draft/pending/approved/rejected, default draft, tracked).
- `hr.attendance.x_approval_required` (Boolean, computed, stored, tracked) — `True` if worked_hours > 12 OR hour ∈ [22..24) ∪ [0..5).
- `hr.attendance.x_approval_by` (M2o `res.users`, readonly) — actor.
- `hr.attendance.x_kiosk_session` (Char) — opaque session id from kiosk cookie; trace-only.
- `hr.attendance.x_face_recognition_data` (Binary, attachment=True) — selfie snapshot.
- `hr.attendance.x_face_recognition_confidence` (Float, readonly) — 0-1, threshold 0.6.
- `hr.attendance.x_payroll_work_entry_id` (M2o `hr.work.entry`, readonly) — payroll link.
- `hr.attendance.x_payroll_synced` (Boolean, readonly, tracked) — flag set when work entry created.
- `custom.attendance.overtime.rule.differential` (Selection: weekday/weekend/holiday) — match key; `holiday` exists as a value but **no public-holiday lookup is done** in `_get_active_overtime_rule` (only weekday vs weekend by `weekday() >= 5`).
- `custom.attendance.overtime.rule.threshold_hours` (Float, default 8.0) — daily threshold above which hours are OT.
- `custom.attendance.overtime.rule.multiplier` (Float, default 1.5) — pay multiplier; **stored but not used in compute** (consumed by payroll downstream).

## Public Methods
- `hr.attendance.action_request_approval()` — draft/rejected → pending; schedules manager activity.
- `hr.attendance.action_approve()` / `action_reject()` — Pending-only transitions.
- `hr.attendance.action_create_overtime_work_entry()` — Idempotent OT → hr.work.entry creation.
- `hr.attendance.action_verify_face()` — Bridge to `custom.ai._recommend` for face match.
- `hr.attendance._kiosk_resolve_employee_by_pin(pin)` (`@api.model`) — Look up employee by `pin`.
- `hr.attendance._kiosk_toggle(employee, lat, lng, session_id)` (`@api.model`) — Toggle check-in/out; returns `(record, action)` where action ∈ `{check_in, check_out}`.
- `hr.attendance._get_active_overtime_rule(check_in)` (`@api.model`) — Best-match rule; falls back to any active rule if no differential match.
- `hr.attendance._ensure_overtime_work_entry_type()` — Get-or-create `hr.work.entry.type` code='OT'.
- `hr.attendance._haversine_meters(lat1, lon1, lat2, lon2)` (`@staticmethod`) — Great-circle in metres; earth radius 6,371,000.
- Controller: `GET /custom_attendance/kiosk`, `POST /custom_attendance/kiosk/submit`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `hr_attendance`, `hr_work_entry`, `custom_hr_payroll_id`, `portal`, `mail`.
- **Inherits from:** `hr.attendance` (+ `mail.thread`, `mail.activity.mixin`).
- **Extended by:** none in-tree; payroll consumes `hr.work.entry` records this module creates.
- **External calls:** `custom.ai._recommend(...)` for face verification (best-effort, swallows exceptions).
- **Cross-vertical:** generic attendance capability with Indonesian use-case alignment (Asia/Jakarta timezone assumed).

## Gotchas
- **PIN auth on the kiosk is via `hr.employee.pin`** — a 4-digit numeric field shared across all employees in the company; collisions or PIN guessing trivially impersonate. There's no PIN attempt rate limit.
- **`csrf=False` on the kiosk endpoints** — necessary for the lobby tablet flow but means any page can POST to `/custom_attendance/kiosk/submit`.
- **`auth='public'` controller** uses `sudo()` for attendance writes; multi-tenant deployments rely on dbfilter to isolate.
- **`differential='holiday'` is a Selection value but never used** — `_get_active_overtime_rule` only branches weekday vs weekend; public-holiday OT must be configured manually or extension code.
- **OT multiplier is not applied here** — it's metadata for downstream payroll rules; the `x_overtime_hours` field holds raw hours, not "multiplier-adjusted hours".
- **`_compute_approval_required` does not consider geofence_validated** — being out-of-geofence does not auto-flag for approval.
- **Geofence validation only checks check-in coordinates**, not check-out. Operators can clock out from anywhere.
- **Late-night threshold is hardcoded** (`hour >= 22 or hour < 5`); not configurable per company.
- **Anomaly threshold 12h is hardcoded** in `_compute_approval_required`.
- **Face recognition is a stub** — relies on whatever `custom.ai._recommend` returns; threshold 0.6 hardcoded; failure path is silent (returns False).
- **`x_kiosk_session` is opaque** — same cookie token persists 30 days, same browser → same session_id across many employees; trace value is limited.
- **`unlink` cancels linked work entries** but swallows exceptions; if the cancel fails, the work entry may persist as a dangling draft.
- **`hr.work.entry` vals branch on optional fields** — `date` and `x_source_attendance_id` are only set if present in `_fields`; the latter is an unmodeled hook for cross-module linkage.

## Out of Scope
- **Shift/roster-aware OT** — threshold is per-day, not per-shift; no link to `custom_planning` slots.
- **Public-holiday OT differential** — `differential='holiday'` is a label only.
- **Multi-fence per employee** — single `x_geofence_id` per attendance; assignment is manual or upstream.
- **OT cap / weekly max enforcement** — no compliance gates.
- **Real face recognition implementation** — relies on external `custom.ai` gateway.
- **Cross-day shifts** — `check_in.weekday()` determines differential; an overnight shift split across Sat/Sun uses only the check-in day.
