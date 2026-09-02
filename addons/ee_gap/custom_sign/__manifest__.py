# -*- coding: utf-8 -*-
{
    "name": "Custom Sign",
    "summary": "E-signature workflow with multi-signer routing + token portal",
    "description": """
Lightweight e-signature. sign.request bundles a PDF template + ordered
signer list; each signer gets a tokenised portal link to inspect the
document and submit a typed/drawn signature.
""",
    "author": "Custom Platform",
    "category": "Productivity/Sign",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mail", "portal", "website"],
    "capability_tags": ["approval-workflow", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/sign_template_views.xml",
        "views/sign_request_views.xml",
        "views/portal_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
