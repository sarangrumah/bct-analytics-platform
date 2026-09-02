---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_appointments
manifest_version: 19.0.0.1.0
---

# custom_appointments

## Purpose
Public-facing self-service appointment booking. Visitors hit `/book/<slug>` for a given `appointment.type`, see a generated slot grid built from the resource's working hours, submit a booking form, and create an `appointment.booking` that (if confirmed) materialises a `calendar.event` on the assigned resource's user calendar. Includes capacity-aware overlap protection and PDP audit (classification `pii`).

## Business Flow
- HR/Admin creates `appointment.resource` (name, user_id, timezone default `Asia/Jakarta`, capacity default 1, `working_hours_start/end`, `working_days` CSV like `"1,2,3,4,5"` for Mon-Fri).
- HR/Admin creates `appointment.type` (name, `slug` unique, `duration_minutes`, `buffer_minutes`, `advance_notice_hours` default 4, `max_days_ahead` default 30, `require_confirmation`, `resource_ids` M2M to resources).
- Visitor opens `/book/<slug>` (auth=public, website=True). Controller searches for the active type by slug, picks the first active resource (`resource_ids.filtered("active")[:1]`), builds a slot grid via `_build_slots(atype, resource)`:
  - Iterates `day in range(1, min(max_days_ahead+1, 6))` — capped at next 5 days regardless of `max_days_ahead`.
  - Filters by `working_days` (ISO weekday).
  - For each hour in `[working_hours_start..working_hours_end)`, emits `slot_dt.isoformat()` if `slot_dt >= now + advance_notice_hours`.
  - Renders `custom_appointments.booking_page` with `atype`, `resource`, `slots`.
- Visitor POSTs `/book/<slug>/submit` with `start_dt`, `resource_id`, `customer_name`, `customer_email`, `customer_phone`, `notes`. CSRF is enforced (`csrf=True`).
- `appointment.booking.create` (sudo): assigns `name` from `ir.sequence` code `appointment.booking` (fallback `APT-???`); if the type has `require_confirmation=False`, state defaults to `confirmed`; then `_sync_calendar_event()` creates a `calendar.event` on `resource_id.user_id` (skipped if no user_id) with partner_ids = applicant partner if any.
- Capacity-aware `@api.constrains` `_check_slot` runs: blocks if `start_dt >= end_dt` or if overlap (sudo search of confirmed bookings with `start < self.end_dt AND end > self.start_dt`) count `>= resource_id.capacity`.
- Workflow transitions:
  - `action_confirm()` — pending → confirmed (only from pending); syncs calendar.event; audit `appointment_confirm`.
  - `action_cancel()` — any → cancelled; unlinks `calendar_event_id` if present; audit `appointment_cancel`.
  - `action_done()` — → done; audit `appointment_done`.
  - `action_no_show()` — → no_show; audit `appointment_no_show`.
- Renders `custom_appointments.booking_confirm` on success.

## Key Models
- `appointment.type` — Bookable service definition; unique `slug` constraint.
- `appointment.resource` — Provider/room/agent; working hours + days.
- `appointment.booking` — Booking record; inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.

## Important Fields
- `appointment.type.slug` (Char, required, indexed, **unique**) — URL component for `/book/<slug>`.
- `appointment.type.duration_minutes` (Integer, default 30, required).
- `appointment.type.buffer_minutes` (Integer, default 0) — declared but **NOT used** in `_build_slots` or `_check_slot`.
- `appointment.type.advance_notice_hours` (Integer, default 4) — minimum lead time before booking.
- `appointment.type.max_days_ahead` (Integer, default 30) — but `_build_slots` caps at 5 days (`min(max_days_ahead+1, 6)`).
- `appointment.type.require_confirmation` (Boolean) — drives default state on create.
- `appointment.resource.timezone` (Char, default `Asia/Jakarta`) — declared but **not applied** when generating slots (controller uses `datetime.utcnow()`).
- `appointment.resource.capacity` (Integer, default 1) — overlap constraint pivot.
- `appointment.resource.working_days` (Char, default `"1,2,3,4,5"`) — CSV of ISO weekdays.
- `appointment.resource.working_hours_start` / `working_hours_end` (Float, defaults 9.0 / 17.0) — 24h decimal.
- `appointment.booking.state` (Selection: pending/confirmed/cancelled/done/no_show, default pending, tracked, indexed).
- `appointment.booking.start_dt` / `end_dt` (Datetime, required, tracked).
- `appointment.booking.calendar_event_id` (M2o `calendar.event`, readonly) — synced via `_sync_calendar_event`.
- `appointment.booking.name` (Char, default "New") — assigned from sequence code `appointment.booking`.

## Public Methods
- `appointment.booking.action_confirm()` — pending → confirmed; sync calendar; audit.
- `appointment.booking.action_cancel()` — any → cancelled; unlink calendar event.
- `appointment.booking.action_done()` / `action_no_show()` — terminal transitions.
- `appointment.booking._sync_calendar_event()` — Create/update `calendar.event` on `resource_id.user_id` (no-op if no user).
- `appointment.booking._pdp_audit_classification()` → `"pii"`.
- Controller: `GET /book/<slug>`, `POST /book/<slug>/submit` (CSRF enforced).
- Controller helper: `_build_slots(atype, resource)` — emits ISO datetime strings.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `mail`, `calendar`, `portal`, `website`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` on `appointment.booking`.
- **Extended by:** none in-tree.
- **External calls:** none.
- **Cross-vertical:** generic booking capability.

## Gotchas
- **`buffer_minutes` is declared but never enforced** — back-to-back bookings can run with zero gap.
- **`_build_slots` caps at 5 days** (`min(max_days_ahead+1, 6)`) regardless of `max_days_ahead`. A type set to "60 days ahead" still only shows 5 days of slots in the public page.
- **`datetime.utcnow()` is used for the now cutoff** — `resource.timezone='Asia/Jakarta'` is declared but ignored in slot generation; slots are emitted in UTC ISO strings.
- **Slot grid is hourly** — `for h in range(start_h, end_h)` — ignores `duration_minutes` entirely. A 30-minute appointment still shows hourly slots.
- **Capacity check only counts `state='confirmed'` overlaps** — pending bookings don't count toward capacity. A type with `require_confirmation=True` is vulnerable to overbooking until each pending is confirmed.
- **CSRF is enabled on `/book/<slug>/submit`** (good security, but public forms must include the CSRF token in their template).
- **`_sync_calendar_event` no-ops without `resource.user_id`** — bookings on user-less resources never produce calendar events.
- **`action_cancel` unlinks the calendar event silently** — the event disappears from invitees' calendars without a cancellation notice.
- **`partner_id` is optional on bookings** — but `_sync_calendar_event` only adds partner_ids if `self.partner_id` truthy; visitor bookings have no res.partner unless one is created manually.
- **No reCAPTCHA / rate limit** on the public POST — spammable.
- **The "5 day grid" cap means `max_days_ahead < 5` works correctly but `> 5` is silently ignored.**
- **Working days CSV `"1,2,3,4,5"`** uses ISO weekdays where 1=Mon, 7=Sun.

## Out of Scope
- **Slot picker UX with timezone awareness** — server emits UTC; client-side conversion not provided here.
- **Reminders / SMS / email confirmation** — only chatter, no auto-mail.
- **Payment for bookings** — no `account.move` / `payment.transaction` link.
- **Multi-resource round-robin** — controller picks first active resource only.
- **Recurring bookings.**
- **Resource skills / appointment-type → resource matching beyond M2M.**
- **Buffer time between bookings.**
