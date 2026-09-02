# -*- coding: utf-8 -*-
{
    "name": "Custom Appointments",
    "summary": "Public-facing booking + internal appointment calendar with resource availability",
    "description": """
Self-service appointment booking. Customers visit a public booking
page tied to an appointment.type, pick a free slot, confirm.
Each booking becomes a calendar.event on the assigned resource.
""",
    "author": "Custom Platform",
    "category": "Marketing/Online Appointment",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "mail",
        "calendar",
        "portal",
        "website",
    ],
    "capability_tags": ["appointment-booking", "calendar", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/appointment_type_views.xml",
        "views/appointment_resource_views.xml",
        "views/appointment_booking_views.xml",
        "views/portal_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
