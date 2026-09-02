# -*- coding: utf-8 -*-
{
    "name": "Custom VoIP",
    "summary": "Click-to-call + call logging with multiple SIP/PBX provider adapters",
    "description": """
Lightweight VoIP integration. voip.provider abstraction (Asterisk AMI /
webhook stub), captures inbound/outbound voip.call records linked to
res.partner, click-to-call button on partner form.
""",
    "author": "Custom Platform",
    "category": "Productivity/VoIP",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mail"],
    "capability_tags": ["voip", "crm", "pdp", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/voip_provider_views.xml",
        "views/voip_call_views.xml",
        "views/res_partner_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
