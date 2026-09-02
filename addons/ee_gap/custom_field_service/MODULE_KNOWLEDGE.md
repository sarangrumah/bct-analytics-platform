---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_field_service
manifest_version: 19.0.0.1.0
---

# custom_field_service

## Purpose
Lightweight **field service management (FSM)** module. Work orders are pinned to a customer site, dispatched to a technician with required-skill validation, walked through scheduled → in_progress → on_hold → done, materials consumed are line-itemed, and a customer signature is captured at completion. Sites are PDP-classified as `pii` (address tied to partner); all state transitions write `pdp.audit_log`.

## Business Flow
- Admin sets up `fsm.skill` records (code-unique) and `fsm.technician` records linking `user_id` / `employee_id` with `skill_ids`.
- Customer service creates `fsm.site` records under a `res.partner` capturing address, lat/long, and `access_notes` (gate codes, parking).
- Dispatcher creates `fsm.work.order`: `site_id` + `technician_id` (required) + `scheduled_start` + `scheduled_end` + `required_skill_ids`. `name` from `ir.sequence(fsm.work.order)`. Constraint `_check_schedule` ensures `end > start`. `_check_skills` validates `required_skill_ids ⊆ technician.skill_ids` — raises `UserError` on missing skills.
- Workflow: `action_schedule` (draft→scheduled) → `action_start` (scheduled/on_hold → in_progress, stamp `started_at`) → optional `action_hold` (in_progress→on_hold) → `action_complete` (in_progress/on_hold → done, stamp `completed_at`, compute `duration_hours`).
- During execution, technician adds `fsm.work.order.material` lines: product + quantity + unit_cost; `uom_id` defaults from product, `subtotal = quantity * unit_cost` (Monetary, currency from work-order company).
- At completion, `action_capture_signature(signature_b64, signed_by)` writes the binary signature, `signed_by_name`, `signed_at`, plus a PDP audit log entry.
- `action_cancel` from any state except `done`.
- `fsm.site.work_order_count` and `fsm.technician.open_wo_count` are computed (search-counts; `sudo()`).

## Key Models
- `fsm.site` — Customer site / location (PDP classification `pii`).
- `fsm.technician` — Worker with skill set; optional `user_id` + `employee_id` link.
- `fsm.skill` — Reusable skill tag (code-unique).
- `fsm.work.order` — Dispatched job; full workflow with audit + signature.
- `fsm.work.order.material` — Per-WO material consumption line.

## Important Fields
- `fsm.site.partner_id` (M2o `res.partner`, required, ondelete cascade) — PDP-classified PII anchor.
- `fsm.site.latitude` / `longitude` (Float, digits 10/7) — geocoded coordinates.
- `fsm.site.access_notes` (Text) — gate codes, parking, on-site contact.
- `fsm.technician.skill_ids` (M2m `fsm.skill`) — drives skill validation on WO.
- `fsm.skill.code` (Char, required, unique constraint) — stable external key.
- `fsm.work.order.state` (draft/scheduled/in_progress/on_hold/done/cancelled).
- `fsm.work.order.scheduled_start` / `scheduled_end` (Datetime, required, tracking) — `_check_schedule` constraint.
- `fsm.work.order.started_at` / `completed_at` (Datetime, readonly) — stamped on transitions.
- `fsm.work.order.duration_hours` (Float, computed/stored) — `(completed_at - started_at) / 3600`.
- `fsm.work.order.required_skill_ids` (M2m `fsm.skill`) — `_check_skills` constraint vs technician.
- `fsm.work.order.customer_signature` (Binary) + `signed_at` + `signed_by_name`.
- `fsm.work.order.material_ids` (O2m) → `fsm.work.order.material.subtotal` (Monetary, computed/stored).

## Public Methods
- `fsm.work.order.action_schedule()` — draft → scheduled.
- `fsm.work.order.action_start()` — scheduled/on_hold → in_progress, stamp started_at.
- `fsm.work.order.action_hold()` — in_progress → on_hold.
- `fsm.work.order.action_complete()` — in_progress/on_hold → done, stamp completed_at + duration.
- `fsm.work.order.action_cancel()` — any non-done state → cancelled.
- `fsm.work.order.action_capture_signature(signature_b64, signed_by)` — binary signature + audit log.
- `fsm.work.order._pdp_audit_classification()` — returns `"internal"`.
- `fsm.site._pdp_audit_classification()` — returns `"pii"`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `mail`, `stock`, `product`.
- **Inherits from:** `mail.thread` + `mail.activity.mixin` + `pdp.audited.mixin` (work.order), `pdp.audited.mixin` (site).
- **Extended by:** Field-service vertical modules (telco install, HVAC, marketplace technician dispatch).
- **External calls:** none.
- **Cross-vertical:** generic FSM capability; consumed by telco / utilities verticals.

## Gotchas
- **`stock` is in `depends` but no `stock.move` is created** for material consumption — `fsm.work.order.material` is purely a cost ledger.
- **`fsm.site.country_id` default is `self.env.ref("base.id")`** (Indonesia) — wrong outside ID vertical; matches platform's Indonesian SMB focus but requires override for foreign sites.
- **`_check_skills` does NOT run on technician edit** — only on work-order `create/write` involving `required_skill_ids` or `technician_id`. Removing a skill from a technician with open WOs is silently allowed and breaks the invariant.
- **`signature_b64` is captured raw** without validation (no max size, no MIME sniff). Binary field uses default attachment storage.
- **`duration_hours` is only computed when BOTH `started_at` and `completed_at` are set** — a holding WO shows duration 0 until completion.
- **`fsm.skill.code` uniqueness uses the new `models.Constraint` API** (Odoo 19) — not `_sql_constraints`.
- **`open_wo_count` uses `sudo()`** — bypasses ACLs; counts across all companies.
- **No travel-time / mileage tracking** — only `duration_hours`.

## Out of Scope
- Real-time technician geolocation (only `fsm.site.latitude/longitude`).
- Customer self-service booking portal.
- Recurring service contracts.
- Invoicing — material `subtotal` is informational, not posted to `account.move`.
- Stock reservation of materials.
- Dispatch optimisation (route planning).
- Multi-skill weighted matching.
