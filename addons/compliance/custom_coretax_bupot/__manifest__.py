# -*- coding: utf-8 -*-
{
    "name": "Custom Coretax e-Bupot Unifikasi",
    "summary": "Bukti Potong PPh Unifikasi (PPh 22 / 23 / 4(2) / 15 / 26) "
    "with XML export and DJP number upload per Coretax schema.",
    "description": """
Custom Coretax e-Bupot Unifikasi
================================

Per-period header (``custom.bupot.unifikasi``) and per-cut line
(``custom.bupot.unifikasi.line``) covering PPh 22, 23, 4(2), 15, 26.

* XML export wizard producing e-Bupot Unifikasi v2 payload.
* CSV upload wizard to ingest DJP-assigned bupot numbers after
  acceptance.
* QWeb PDF report "Bukti Potong PPh Unifikasi".
* All create/write/unlink events flow through ``pdp.audited.mixin``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Compliance/Coretax",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "custom_coretax", "account", "mail"],
    "capability_tags": ["indonesian-tax", "coretax", "withholding", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/bupot_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "wizards/bupot_xml_export_wizard_views.xml",
        "wizards/bupot_number_upload_wizard_views.xml",
        "reports/bupot_pdf_report.xml",
        "views/bupot_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
