# -*- coding: utf-8 -*-
{
    "name": "Custom BAST",
    "summary": "Berita Acara Serah Terima — generic handover document with dual signatures and audit",
    "description": """
Reusable abstraction for Indonesian-style handover documents (BAST).

Provides:

- `custom.bast.document`: handover record with kind (pickup/return/delivery/installation/handover),
  parties, dual signatures with timestamps, optional GPS, audit-logged state machine.
- `custom.bast.line`: itemised lines with condition, photo and optional lot.
- QWeb PDF report.
- Sign wizard for capturing party signatures.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "mail", "stock", "sale"],
    "capability_tags": ["audit-trail", "field-service", "wms"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "wizards/bast_sign_wizard_views.xml",
        "views/bast_document_views.xml",
        "views/menu_views.xml",
        "reports/bast_report.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
