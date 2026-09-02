# -*- coding: utf-8 -*-
{
    "name": "Custom Barcode",
    "summary": "EE-equivalent mobile-friendly barcode scan-in/scan-out for stock pickings (CE)",
    "description": """
Custom Barcode is a CE-compatible alternative to the EE-only stock_barcode app.
It builds on top of the CE `barcodes` module and `barcodes_gs1_nomenclature` to
provide a mobile-friendly scan session wizard for stock.picking flows.

EE-equivalent feature set:
- Real apply-to-picking: updates stock.move.line.qty_done and lot_id
- Batch picking session: scan across many pickings, auto-distribute lines
- Cluster picking: group lines by source location for efficient pickup
- GS1 AI parsing (GTIN, lot, exp, weight) stored on the scan line
- Mobile/kiosk-friendly form view with large buttons
- QWeb-PDF picking barcode summary report (scanned vs expected, deviation %)
- PDP audit logging via pdp.audited.mixin
""",
    "author": "Custom Platform",
    "category": "Inventory/Barcode",
    "version": "19.0.2.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_product_barcode",
        "barcodes",
        "barcodes_gs1_nomenclature",
        "stock",
    ],
    "capability_tags": ["barcode-scan", "wms", "hht", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/cron_data.xml",
        "views/custom_barcode_scan_line_views.xml",
        "views/custom_barcode_scan_session_views.xml",
        "views/custom_barcode_batch_session_views.xml",
        "views/custom_barcode_cluster_run_views.xml",
        "views/custom_barcode_format_views.xml",
        "views/custom_label_template_views.xml",
        "views/custom_printer_config_views.xml",
        "views/custom_print_queue_views.xml",
        "views/menu_views.xml",
        "report/picking_barcode_summary.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
