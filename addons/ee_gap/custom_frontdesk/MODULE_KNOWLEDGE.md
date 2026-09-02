---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_frontdesk
manifest_version: 19.0.0.1.0
---

# custom_frontdesk

## Purpose
Standalone visitor-management module (no CE/EE equivalent in Odoo 19): captures `custom.frontdesk.visitor` records at named `custom.frontdesk.station` stations, generates a one-time `kiosk_token` + PNG QR for pre-registration self-check-in, notifies the host via WhatsApp (using `custom_whatsapp.whatsapp.message`), tracks a workflow `expected → checked_in → checked_out / cancelled`, group-restricts KTP/Passport access, and exposes `export_anonymized()` to mask `id_number` for compliance dumps. Visitors are PDP-audited via `pdp.audited.mixin`.

## Business Flow
- A receptionist or host pre-creates a visitor with `state=expected`, host (`hr.employee`), station, ETA, KTP/Passport, photo, phone.
- Host clicks `action_preregister_visitor()` → generates `kiosk_token = secrets.token_urlsafe(24)`, sends `mail_template_preregister_visitor` email (best-effort), and creates a draft `whatsapp.message` (template `whatsapp_template_host_notify` referenced) with the QR self-check-in URL `<web.base.url>/custom_frontdesk/kiosk_checkin/<token>`. `qr_code_image` is a non-stored compute that renders a PNG via the `qrcode` library (no-op if the lib is missing).
- At the kiosk the visitor scans the QR → controller hits `_check_in_by_token(token)` which validates (`missing/unknown/used/cancelled` raise `UserError`), and if `state=expected` flips to `checked_in`, stamps `check_in_time`, marks `kiosk_token_used=True`, and triggers host WhatsApp via `_notify_host_whatsapp()`.
- Alternative path: receptionist clicks `action_check_in()` directly (no QR) — same notification path.
- Host receives WhatsApp via `whatsapp.message` (template body with `{{name}}/{{company}}/{{station}}/{{purpose}}` literal-substitution, NOT Meta `{{1}}` positional form; the `whatsapp.template` Meta sync handles positional translation elsewhere).
- `action_check_out` writes `check_out_time`; `action_cancel` flips to cancelled.
- `action_print_badge` returns the QWeb report action `action_report_visitor_badge` (badge contains the QR if it was rendered).
- `action_view_visits_for_partner` opens the list of all visits for the same `res.partner`.
- `export_anonymized()` returns a list of dicts where `id_number` is masked to `****<last4>` regardless of caller permissions (uses `sudo()` then masks). Field-level `id_number` is `groups="custom_frontdesk.group_manager"` so non-managers don't see the raw value in the UI either.

## Key Models
- `custom.frontdesk.visitor` — Visitor record (`mail.thread + pdp.audited.mixin`).
- `custom.frontdesk.station` — Named station (kiosk) per company.

## Important Fields
- `custom.frontdesk.visitor.state` (Selection: expected/checked_in/checked_out/cancelled) — workflow.
- `custom.frontdesk.visitor.host_employee_id` (M2o `hr.employee`, required, tracked) — notification target.
- `custom.frontdesk.visitor.station_id` (M2o `custom.frontdesk.station`, required).
- `custom.frontdesk.visitor.partner_id` (M2o `res.partner`, indexed) — historical aggregation key.
- `custom.frontdesk.visitor.id_number` (Char, `groups="custom_frontdesk.group_manager"`) — KTP/Passport, manager-only.
- `custom.frontdesk.visitor.kiosk_token` (Char, indexed, single-use) / `kiosk_token_used` (Boolean) — QR self check-in credentials.
- `custom.frontdesk.visitor.qr_code_image` (Binary, computed, non-stored) — PNG of `<web.base.url>/custom_frontdesk/kiosk_checkin/<token>`.
- `custom.frontdesk.visitor.badge_number` (Char, default `ir.sequence.next_by_code('custom.frontdesk.visitor.badge')`).
- `custom.frontdesk.visitor.whatsapp_notified` (Boolean).
- `custom.frontdesk.visitor.check_in_time` / `check_out_time` (Datetime).

## Public Methods
- `custom.frontdesk.visitor.action_check_in() / action_check_out() / action_cancel()` — workflow buttons.
- `custom.frontdesk.visitor.action_preregister_visitor()` — generates token + email + WhatsApp.
- `custom.frontdesk.visitor.action_print_badge()` — QWeb report action.
- `custom.frontdesk.visitor.action_view_visits_for_partner()` — opens partner's visit list.
- `custom.frontdesk.visitor._check_in_by_token(token)` (`@api.model`) — kiosk controller entrypoint (controller in module).
- `custom.frontdesk.visitor.export_anonymized()` — masked-id dumps for compliance.
- `custom.frontdesk.visitor._notify_host_whatsapp() / _send_preregister_whatsapp() / _render_host_notify_body(template)` — WhatsApp dispatchers.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `custom_whatsapp`, `hr`, `mail`, `web`.
- **External python:** `qrcode` (optional — falls back to no QR if missing).
- **Inherits from:** `mail.thread`, `pdp.audited.mixin`.
- **Extended by:** none declared.
- **External calls:** outbound via `custom_whatsapp` (creates `whatsapp.message` records, queued); SMTP via `mail.template.send_mail`.
- **Cross-vertical:** generic — could be reused by any tenant needing visitor logs.

## Gotchas
- **WhatsApp send is best-effort and silent** — every `_notify_host_whatsapp` exception is caught and only logged. Failures are not visible from the visitor form.
- **`whatsapp.message` is created but `action_send()` is not invoked here** — the WA platform queue is expected to pick up draft messages elsewhere. If no queue is running, messages stay drafted.
- **`qr_code_image` is a non-stored compute** — every read re-renders the PNG via `qrcode`. Heavy on list views; the field is generally only read on the form / report.
- **`id_number` field-level groups protection is one of TWO layers** — `export_anonymized()` ALWAYS masks regardless of group. The field is also `attachment=True` for `photo`, not `id_number` (which is plain Char).
- **Kiosk token is single-use** (`kiosk_token_used=True`) — re-printing the badge does NOT regenerate the token; a used token cannot self-check-in again (you must use `action_check_in`).
- **Pre-register WhatsApp uses inline Indonesian-language body** (not the template), but `_notify_host_whatsapp` uses `template.body_text` if available. The two flows diverge in source-of-truth.
- **`whatsapp_notified` flag** flips True the first time WA notification SUCCEEDS — re-checking in (e.g. on a second visit) will set it again.
- **No idempotency on `_notify_host_whatsapp`** — if `action_check_in` is called twice, the host gets two WhatsApp messages.
- **`station_id.company_id` is captured but not enforced** as a multi-company filter on visitor records — a visitor at station A (company 1) could be created by a user in company 2 with `sudo()`.

## Out of Scope
- Visitor pre-screening / NDA acceptance.
- Multi-tenant station kiosks beyond `company_id` on station.
- Real-time host presence check (does host need to be in the office today?).
- Integration with access-control hardware (door / badge readers).
- Visitor photo capture from kiosk camera (only file upload of `photo` field).
- Compliance retention policy / auto-purge of old visitor records.
