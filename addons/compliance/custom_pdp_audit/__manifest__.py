# -*- coding: utf-8 -*-
{
    "name": "Custom PDP Audit",
    "summary": "Append-only hash-chained audit log (UU 27/2022)",
    "description": "Odoo bridge to the Postgres-side pdp.audit_log (append-only, sha256-chained).",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Compliance/PDP",
    "version": "19.0.0.3.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_taxonomy"],
    "capability_tags": ["pdp", "audit-trail", "compliance"],
    "data": [
        "security/pdp_security.xml",
        "security/ir.model.access.csv",
        "views/pdp_audit_log_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
    "pre_init_hook": "pre_init_hook",
}
