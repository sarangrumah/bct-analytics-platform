# Custom Field Service

Technician dispatch + work-order tracking for on-site service teams.

## Models

- `fsm.site` — customer location (lat/lon, access notes).
- `fsm.skill` — skill catalogue (electrical, plumbing, HVAC, ...).
- `fsm.technician` — links to `res.users` + `hr.employee`; has many skills.
- `fsm.work.order` — state machine `draft → scheduled → in_progress →
  done` (+ `on_hold` + `cancelled`). Tracks duration, materials, customer
  signature.
- `fsm.work.order.material` — line items consumed during a WO.

Constraint: WO refuses save if technician lacks any of the required skills.

## Security
- `custom_field_service.group_fsm_technician` — view sites/skills, work
  on assigned WOs.
- `custom_field_service.group_fsm_dispatcher` — schedule, reassign,
  manage skill catalogue. Inherits technician.

## Audit
Every WO state change writes to `pdp.audit_log` (chained).
