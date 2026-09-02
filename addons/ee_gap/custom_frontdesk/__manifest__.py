# -*- coding: utf-8 -*-
{
    "name": "Custom Frontdesk",
    "summary": "Visitor management with host notification and PDP audit",
    "description": """
Custom Frontdesk is a CE-targeted standalone visitor management module
(no equivalent in Odoo 19 CE) covering:

- Visitor check-in / check-out at office stations
- Host notification via custom_whatsapp (stub for now)
- PDP audit on visitor records (KTP/Passport restricted to Manager)
""",
    "author": "Custom Platform",
    "category": "Human Resources/Frontdesk",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_whatsapp",
        "hr",
        "mail",
        "web",
    ],
    "capability_tags": ["visitor-management", "whatsapp", "qr-checkin", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/custom_frontdesk_station_views.xml",
        "views/custom_frontdesk_visitor_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "custom_frontdesk/static/src/css/kiosk.css",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
