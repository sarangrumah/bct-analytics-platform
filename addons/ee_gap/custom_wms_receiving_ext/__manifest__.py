# -*- coding: utf-8 -*-
# License: LGPL-3
{
    "name": "Custom WMS Receiving Extensions",
    "summary": "GR completeness: GS1 expiry write-through, supplier batch on lots, receipt template import (CSV/XLSX)",
    "description": """
WMS Receiving Extensions
========================
Closes the goods-receipt gaps of the WMS stack (client requirements 3 & 4)
without touching the shared ``custom_barcode`` addon:

- **GS1 expiry write-through** — AI 17 (expiration date) was parsed into the
  scan line's ``x_gs1_parsed`` JSON but never applied; it now lands on
  ``stock.lot.expiration_date`` when the scan is applied to the picking.
- **Supplier batch reference** — new field on ``stock.lot`` and on the scan
  line; filled manually or from the GS1 lot (AI 10) when applied.
- **Serial / IMEI capture** — GS1 AI 21 becomes the ``stock.lot`` name for
  serial-tracked products, and a bare 14–16 digit IMEI scan is attributed to
  the picking's sole serial-tracked product instead of being dropped as
  "not found".
- **Receipt template import** — wizard on incoming pickings to upload a
  CSV/XLSX template (barcode/SKU, serial or lot, qty, expiry date, supplier
  batch) that creates move lines + lots in bulk; includes a downloadable
  blank template.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Barcode",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "stock",
        "product_expiry",
        "custom_barcode",
        "custom_product_barcode",
    ],
    "capability_tags": ["wms", "barcode-scan", "goods-receipt"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "views/stock_lot_views.xml",
        "views/scan_session_views.xml",
        "wizard/receipt_import_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
