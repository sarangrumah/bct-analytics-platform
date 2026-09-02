---
status: reviewed
generated_at: 2026-07-30T00:00:00Z
generator: hand-written
module: custom_project_notify
manifest_version: 19.0.1.0.0
---

# custom_project_notify

## Purpose
Rule-driven notifications for project / CR / task / weekly events. The event is born in
Odoo and dispatched to the Next.js BFF over HMAC, which renders and sends WhatsApp +
e-mail.

## Why the event is born here, not in the BFF
e-Telekomunikasi puts its notification calls in the Next.js route handlers, which works
because Next.js is its only writer. Here there are four: the VAS PMO UI, the Odoo backend,
the Jira webhook, and the ticket bridge. Wiring notifications into the BFF alone would
leave three of those four silent. So every tracked change writes an outbox row whatever
its origin, and the BFF stays a single renderer/sender.

## Business Flow
`_pmo_dispatch(event)` → read `custom.project.notify.rule` for that event → resolve
recipients per kind → Odoo channel handled inline (`message_post` + `activity_schedule`
for events that need action) → WhatsApp/e-mail queued in `custom.project.notify.outbox` →
`cron_dispatch()` every minute POSTs to `<bff>/api/notify` signed
`HMAC-SHA256(secret, ascii(ts) + raw_body)` → the BFF's per-channel results are mirrored
into `custom.project.notify.log`.

Retry is 5 attempts with 1/5/15/60/240-minute backoff, then `state=failed` with an
operator **Retry** button. An unconfigured BFF leaves the row `pending` and burns no
attempts — the queue survives a BFF that is not deployed yet.

## Key Models
- `custom.project.notify.rule` — event × recipient kind × channels. 40 rows seeded
  `noupdate="1"` so an upgrade never overwrites what the PO Lead tuned.
- `custom.project.notify.outbox` — the queue; `payload_json` is what the BFF receives.
- `custom.project.notify.log` — one row per channel per recipient. Phone numbers are
  masked (`mask_phone`) because this log is read by many people.
- `pmo.notify.source` (AbstractModel) — the shared behaviour; concrete models only
  answer "who is the assignee / PO / brand PIC for me".

## Important Fields
- `rule.recipient_kind` — assignee / reporter / ba / po / portfolio_owner /
  vertical_owner / **brand_pic** / group. `brand_pic` resolves to
  `custom.project.vertical.pic_partner_ids` — people outside the team.
- `log.skipped_reason` — set when a channel was never attempted (no number, no address).
  "Nobody was reachable" is recorded as a finding rather than swallowed.

## Public Methods
- `outbox.enqueue(record, event, recipients, extra=None)` — called with an empty recipient
  list on purpose when the rules matched but nobody was reachable.
- `outbox.cron_dispatch(limit=100)` — never raises; one bad row cannot stop the queue.
- `outbox.action_retry()`.
- `_pmo_notify_event` / `_pmo_notify_project_event` / `_cr_notify_event` /
  `_pmo_notify_weekly_event` — the hook implementations.

## Integration Points
- **Depends on:** `custom_project_portfolio`, `custom_project_cr`.
- **Config:** `custom_project_notify.bff_url` (default `http://vas-pmo:8080`),
  `custom_core.secure_endpoint.pmo.secret`, `custom_project_notify.dispatch_enabled`.
- Three logs are kept separate on purpose: `pdp.audit.log` (who changed what),
  this module's log (was the human told), `adapter.call.log` (did the outside world answer).

## Gotchas
- `_pmo_dispatch` swallows every exception by design: a notification must never break
  the transaction that triggered it. Failures land in the server log and the delivery log.
- Odoo 19 merged `res.partner.mobile` into `phone`; `_pmo_contact` probes the field so
  it also works on an older tenant.
- `custom.weekly.progress` has no chatter, so the Odoo channel degrades to nothing there —
  the mixin checks for `message_post` first.

## Tests
`tests/test_notify_outbox.py` (13), tag `pmo`. The load-bearing one is
`test_orm_stage_change_is_not_silent`: a write straight on the ORM — what a Jira webhook or
the Odoo backend does — must still raise the event. Also covers payload shape, brand-PIC
targeting, the no-recipient finding, backoff-then-give-up, phone masking, rules-as-data,
and that work on hold is never chased for being late.
