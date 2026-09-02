# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Transfer Order Engine",
    "summary": "Rule-driven internal transfer orchestration (low-water mark, expiry, consolidation)",
    "description": """
Generic transfer-order (TO) engine that fires on rule-driven triggers
(low-water mark, expiry approaching, zone consolidation, picking
replenishment, manual). Materializes proposals into stock.move internal
transfers and includes a barcoded pick slip QWeb report.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.3.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "stock",
        "product",
        "barcodes",
        "mail",
    ],
    "capability_tags": ["wms", "barcode-scan", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/cron.xml",
        "views/to_rule_views.xml",
        "views/transfer_order_views.xml",
        "wizard/manual_to_wizard_views.xml",
        "reports/to_pick_slip_report.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
