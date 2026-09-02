# -*- coding: utf-8 -*-
{
    "name": "Custom Events",
    "summary": "Events CE extension: WhatsApp tickets w/ QR PNG, QR check-in URL, sponsors, tracks, post-event survey, capacity/waitlist",
    "description": """
Custom Events extends Odoo CE `event` with EE-equivalent features:

- WhatsApp ticket delivery via custom_whatsapp with template variable
  substitution ({{event_name}}, {{date}}, {{qr_url}}) and an attached
  QR code PNG generated from x_qr_token (stdlib `qrcode`).
- Public QR check-in HTTP controller at /custom_events/checkin/<token>.
- Sponsor tracking (custom.event.sponsor: logo, tier, amount, benefits).
- Multi-session / multi-track support via standard event.track
  (website_event_track) plus per-registration track preferences.
- Post-event survey trigger via daily cron after x_end_date.
- Capacity & waitlist: registrations created when full go to 'waitlist'
  with a manual / cron-driven promotion action.
- Midtrans/Xendit payment integration via custom_payment_id
- PDP consent gating for marketing follow-up
""",
    "author": "Custom Platform",
    "category": "Marketing/Events",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_pdp_consent",
        "event",
        "website_event_track",
        "survey",
        "custom_whatsapp",
        "custom_payment_id",
    ],
    "capability_tags": ["marketing", "whatsapp", "qr-checkin", "pdp", "barcode-scan"],
    "external_dependencies": {
        "python": ["qrcode"],
    },
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/event_event_views.xml",
        "views/event_registration_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
