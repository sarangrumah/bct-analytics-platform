# -*- coding: utf-8 -*-
# License: LGPL-3
{
    "name": "Custom WMS Reports",
    "summary": "WMS reporting pack: purchase return, stock summary (qty+value), stock take, spot check, transfer",
    "description": """
WMS Reporting Pack
==================
Closes the reporting gaps of the WMS stack (client requirements 11-15):

- **Purchase Return Report** — done moves to supplier locations, grouped
  per supplier / per SKU (list + pivot).
- **Stock Summary Report** — on-hand per SKU / warehouse / location with
  unit cost and stock value (list + pivot).
- **Stock Take Report** — cycle-count lines with expected/counted/variance
  qty and variance value (list + pivot) + printable PDF sheet per session.
- **Spot Check** — new ``spot_check`` sampling method on cycle-count plans
  (small random sample) + dedicated report view filtered on it.
- **Transfer Report** — stock moves by operation type with demand/done qty
  (list + pivot).
- **Scrap Report** — write-offs per bin / SKU / lot with scrap value and the
  replenish flag (list + pivot) + printable Scrap Note PDF.

All analysis models are read-only SQL views. Each one also exports to
**XLSX with embedded Code128 barcodes** — one column for the transaction
(picking / scrap order / count session / bin) and one for the line item
(lot when tracked, else the product EAN) — so the sheet stays scannable
outside Odoo. The PDFs carry the same two barcode levels.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Reporting",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "stock_account",
        "purchase_stock",
        "custom_wms_cycle_count",
        "custom_wms_docs",
    ],
    "capability_tags": ["wms", "reporting"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "report/stock_take_report_pdf.xml",
        "report/scrap_note_pdf.xml",
        "views/purchase_return_report_views.xml",
        "views/scrap_report_views.xml",
        "views/stock_summary_report_views.xml",
        "views/stock_take_report_views.xml",
        "views/transfer_report_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
