# -*- coding: utf-8 -*-
{
    "name": "Custom Spreadsheet",
    "summary": "Workbook layer with CSV import/export, AI helpers, versioning, sharing",
    "description": """
Custom Spreadsheet (EE-equivalent Tier-3 layer) provides:

- Workbook + tag model with JSON-encoded grid storage
- Real CSV import wizard (parses csv into sheets/cells)
- Real CSV export (ir.attachment download)
- AI helpers: ask AI, formula suggestion, data cleaning report
- Load records from any Odoo model into a workbook sheet
- Version snapshots on every data change with restore
- Public share link (read-only HTML table render)
- PDP audit trail via pdp.audited.mixin

The interactive grid renderer remains delegated to Odoo 19's CE
`spreadsheet` engine; this module focuses on the workbook layer.
""",
    "author": "Custom Platform",
    "category": "Productivity/Spreadsheet",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_documents",
        "custom_ai_bridge",
    ],
    "capability_tags": ["ai", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/custom_spreadsheet_tag_views.xml",
        "views/custom_spreadsheet_version_views.xml",
        "views/custom_spreadsheet_workbook_views.xml",
        "views/custom_spreadsheet_import_wizard_views.xml",
        "views/custom_spreadsheet_load_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
