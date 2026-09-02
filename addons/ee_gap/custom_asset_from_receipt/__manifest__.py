# -*- coding: utf-8 -*-
{
    "name": "Custom Asset From Receipt",
    "summary": "Bulk-convert received serial-numbered products into Fixed Assets + Rental Assets via a per-item picker wizard",
    "description": """
Bridges ``custom_accounting_asset`` and ``custom_rental`` so a single
goods receipt (e.g. 200 drones with 200 serial numbers) can spawn one
``custom.fixed.asset`` per SN — and optionally one ``rental.asset`` per
SN — through a wizard with per-row select checkboxes and a Select All
shortcut. Idempotent: previously-converted serial numbers are detected
and disabled in the wizard.
""",
    "author": "Custom Platform",
    "category": "Inventory/Inventory",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "stock",
        "purchase",
        "account",
        "custom_accounting_asset",
        "custom_rental",
    ],
    "capability_tags": ["fixed-assets", "rental", "inventory"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "views/product_template_views.xml",
        "views/stock_picking_views.xml",
        "views/purchase_order_views.xml",
        "wizard/asset_conversion_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
