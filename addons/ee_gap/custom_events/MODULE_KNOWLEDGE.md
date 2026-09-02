---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_events
manifest_version: 19.0.0.2.0
---

# custom_events

## Purpose
Extends CE `event.event` + `event.registration` with EE-equivalent features adapted to the Indonesian market: per-registration QR token + public QR check-in route, WhatsApp ticket delivery via `custom_whatsapp` templates, multi-tier sponsor tracking (`custom.event.sponsor`), multi-session via standard `event.track` (`x_has_tracks` flag), a daily post-event survey cron, and PDP consent gating for marketing follow-up. Capacity / waitlist is implemented by an `action_promote_waitlist` button.

## Business Flow
- An organiser configures an `event.event`: optionally sets `x_whatsapp_ticket_template_id`, picks `x_marketing_consent_purpose`, enables `x_qr_checkin_enabled`, flips `x_has_tracks` for multi-session, links `x_post_event_survey_id`, sets `x_end_date` to override `date_end`.
- Sponsors are added as `custom.event.sponsor` rows (tier=platinum/gold/silver/bronze, logo, `amount_paid`, benefits, `website_url`).
- A visitor registers; `event.registration.create` auto-generates `x_qr_token = secrets.token_urlsafe(16)`.
- Organiser clicks "Send WhatsApp Ticket" → `action_send_whatsapp_ticket()` creates one `whatsapp.message` per registration using the event template + partner phone, calls `action_send()`, and stamps `x_whatsapp_ticket_sent=True`.
- At the door, a kiosk hits `/custom_events/checkin/<token>` (controller in this module, not shown in models) which calls `action_qr_checkin(token)` → returns a JSON dict with `ok / already / attendee / event / checked_in_at`. State of CE `event.registration` is flipped to `done` (`attended`) when the field exists.
- Manual check-in from the form: `action_manual_checkin()` re-uses the QR path.
- Daily cron `_cron_send_post_event_survey` finds events whose `x_end_date or date_end < now`, not yet `x_post_event_survey_sent`, with a survey set; for each open registration with email, sends `mail_template_post_event_survey` carrying the survey start URL.
- `action_promote_waitlist()` (event-level) selects registrations in state `waitlist` and calls `action_promote_from_waitlist()` on them (method assumed to exist on the inherited CE event.registration; not defined in this module).

## Key Models
- `event.event` (inherited) — Adds WhatsApp template, QR enable, sponsors, tracks flag, post-event survey + extended end date.
- `event.registration` (inherited) — Adds QR token, check-in stamps, WhatsApp-sent flag, QR check-in action.
- `custom.event.sponsor` — Per-event sponsor with tier, logo, paid amount, benefits, website.

## Important Fields
- `event.event.x_whatsapp_ticket_template_id` (M2o `whatsapp.template`) — template for ticket delivery.
- `event.event.x_marketing_consent_purpose` (Selection: event_followup / none) — PDP gate for post-event marketing.
- `event.event.x_qr_checkin_enabled` (Boolean, default True) — guards public check-in route.
- `event.event.x_has_tracks` (Boolean) — UI hint to expose tracks.
- `event.event.x_post_event_survey_id` (M2o `survey.survey`) — survey link sent after event.
- `event.event.x_post_event_survey_sent` (Boolean, copy=False) — idempotency latch for cron.
- `event.event.x_end_date` (Datetime) — overrides `date_end` for survey cron timing.
- `event.registration.x_qr_token` (Char, indexed, copy=False, secrets.token_urlsafe(16)) — check-in identifier.
- `event.registration.x_checked_in_at` / `x_checked_in_by_user_id` (Datetime, M2o) — check-in audit.
- `event.registration.x_whatsapp_ticket_sent` (Boolean, tracked) — idempotency for WA send.
- `custom.event.sponsor.tier` (Selection: platinum/gold/silver/bronze, indexed).
- `custom.event.sponsor.amount_paid` (Monetary, currency from event.company).

## Public Methods
- `event.event._cron_send_post_event_survey()` (`@api.model`) — daily survey dispatch.
- `event.event.action_promote_waitlist()` — promotes waitlisted regs (delegates to standard registration method).
- `event.registration.action_send_whatsapp_ticket()` — WhatsApp ticket dispatch (best-effort).
- `event.registration.action_qr_checkin(qr_token)` (`@api.model`) — JSON-serialisable check-in endpoint.
- `event.registration.action_manual_checkin()` — re-runs QR path from form button.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_consent`, `event`, `website_event_track`, `survey`, `custom_whatsapp`, `custom_payment_id`.
- **External python:** `qrcode` (stated in manifest; used to render PNG QR for tickets via the controller / report — not invoked directly in `models/`).
- **Inherits from:** `event.event`, `event.registration` (CE).
- **Extended by:** none declared.
- **External calls:** outbound WhatsApp via `custom_whatsapp` (creates `whatsapp.message`); SMTP via `mail.template.send_mail` for survey.
- **Cross-vertical:** generic; the WhatsApp ticket and QR check-in are reusable for any in-person workflow.

## Gotchas
- **`action_promote_from_waitlist` on `event.registration` is NOT defined in this module** — `action_promote_waitlist` calls into it as if it exists. Promotion will raise `AttributeError` unless another module (or vertical) provides this method.
- **QR check-in flips registration state to `done`** if the field exists, swallowing `UserError` — CE state machine guards are not respected.
- **`x_qr_token` is `secrets.token_urlsafe(16)`** (~22 chars) — short enough for QR but not collision-proof at very large scale.
- **Survey URL fallback (`/survey/start/<access_token>`) hard-codes the path** — if upstream changes the route this breaks silently.
- **`_cron_send_post_event_survey` has no recipient deduplication beyond `x_post_event_survey_sent` on the event**; a registration created AFTER the cron has already run will never receive the survey for that event.
- **PDP consent gate (`x_marketing_consent_purpose`) is read but the cron does NOT filter recipients by consent** — only the value is captured. Consent enforcement is the survey/marketing-automation module's responsibility.
- **WhatsApp ticket send catches all exceptions per registration** and only logs a warning — failure is invisible from the UI.
- **`custom_payment_id` is in depends** — implies Midtrans/Xendit integration for paid events, but this module doesn't reference the payment fields directly; integration is in the views / payment module.

## Out of Scope
- Real waitlist promotion logic (relies on another module's `action_promote_from_waitlist`).
- Recurring events.
- Multi-language ticket / survey templates.
- Badge printing (the QR is on the ticket only).
- Refunds on cancellation.
- Per-track capacity enforcement.
