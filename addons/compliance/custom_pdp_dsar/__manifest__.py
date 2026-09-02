# -*- coding: utf-8 -*-
{
    "name": "Custom PDP DSAR",
    "summary": "Data Subject Access Requests under UU 27/2022",
    "description": "Receive, verify, gather, and deliver DSARs. Includes anonymization helper.",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Compliance/PDP",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_taxonomy", "custom_pdp_audit", "custom_ai_bridge"],
    "capability_tags": ["pdp", "compliance", "audit-trail", "dsar", "ai"],
    "data": [
        "security/pdp_security.xml",
        "security/ir.model.access.csv",
        "views/pdp_dsar_request_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
