---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_planning
manifest_version: 19.0.0.1.0
---

# custom_planning

## Purpose
Lightweight resource-planning / shift-scheduling module. Define `planning.role` records, assign `hr.employee` to roles, and create `planning.slot` shifts (start/end, optional employee) with overlap protection per employee. Slots progress through `open → assigned → published → cancelled`. State changes are audited via `pdp.audited.mixin`.

## Business Flow
- HR creates `planning.role` rows (name, color, `employee_ids` M2M of eligible employees).
- Manager creates `planning.slot` (role_id, employee_id optional, start_dt, end_dt, state `open`).
- `_compute_name` derives `"<role>: <employee or 'Open'> @ <start_dt>"`.
- `_compute_duration` derives `duration_hours = (end_dt - start_dt) / 3600`.
- `@api.constrains` `_check_overlap` enforces:
  - `start_dt < end_dt` else `ValidationError("End must be after start.")`.
  - If `employee_id` set, search any other slot for the same employee in state `assigned` or `published` overlapping `[start_dt, end_dt)`; raise if any.
- Transitions:
  - `action_assign(employee_id)` — writes `employee_id` + state `assigned`; audit `planning_assign` (payload: `{employee_id}`).
  - `action_publish()` — requires `employee_id`; → `published`; audit `planning_publish`.
  - `action_cancel()` — → `cancelled`; audit `planning_cancel`.

## Key Models
- `planning.role` — Role definition with eligible employee pool.
- `planning.slot` — Shift assignment; inherits `mail.thread`, `pdp.audited.mixin`.

## Important Fields
- `planning.slot.state` (Selection: open/assigned/published/cancelled, default open, tracked).
- `planning.slot.role_id` (M2o `planning.role`, required, indexed).
- `planning.slot.employee_id` (M2o `hr.employee`, indexed, tracked) — empty = open shift; anyone in the role can claim.
- `planning.slot.start_dt` / `end_dt` (Datetime, required, tracked).
- `planning.slot.duration_hours` (Float, computed, stored).
- `planning.slot.name` (Char, computed, stored) — `"<role>: <who> @ <start_dt>"`.
- `planning.role.employee_ids` (M2M `hr.employee` via `planning_role_employee_rel`).

## Public Methods
- `planning.slot.action_assign(employee_id: int)` — Assign + audit.
- `planning.slot.action_publish()` — Requires assignee; → published.
- `planning.slot.action_cancel()` — → cancelled.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `hr`, `mail`.
- **Inherits from:** `mail.thread`, `pdp.audited.mixin` on `planning.slot`.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic shift-scheduling capability.

## Gotchas
- **Overlap check ignores `open` slots** — only `assigned`/`published` overlaps trigger. Two `open` slots for the same employee are accepted, then resolving them with `action_assign` may then start failing.
- **No enforcement that `employee_id` is in `role_id.employee_ids`** — anyone can be assigned to any slot regardless of role membership.
- **`_check_overlap` uses `sudo().search`** — multi-company isolation depends on `company_id` being set; ACLs ignored at constraint time.
- **No reopen / unassign transition** — once cancelled, no way back to open via provided actions; would require direct write.
- **`action_assign(employee_id)` receives a raw int**, not a Many2one — caller responsibility to pass valid id.
- **`duration_hours` is naive** — no working-calendar awareness; midnight-crossing or DST shifts will be miscounted by exact seconds.
- **No multi-slot publication** — `action_publish` works one record at a time effectively (still loops `self`, but no batch confirmation UI).
- **No connection to `custom_attendance` or `custom_timesheet`** — published slots are not auto-compared with actual worked hours.
- **`company_id` defaulted but no record rule shown here** — relies on `custom_core` / security xml for multi-company scoping.

## Out of Scope
- **Auto-fill / suggested employee assignment** — no algorithm.
- **Shift swap requests** — no peer-to-peer reassignment workflow.
- **Recurring shift templates** — single instances only.
- **Open-shift claim by employee** — `employee_id` empty + state `open` is supported, but there's no `action_claim()` self-service method.
- **Working time / calendar integration.**
- **Cost / margin per shift.**
- **Skills matching beyond role membership.**
