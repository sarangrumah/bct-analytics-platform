# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Inbound QC",
    "summary": "Inbound quarantine: QC gate on receipts, non-reservable inbound stock, unknown-item registration",
    "description": """
Closes three inbound gaps that plain Odoo 19 CE leaves open:

* **Quarantine that actually quarantines.** Stock sitting in an inbound / QC
  area is excluded from outbound reservation at the ``stock.quant`` gather
  level, so a sales delivery can never promise goods that have not been
  inspected. Native Odoo would happily reserve them.
* **A QC gate on receipts.** Picking types can require inspection; the receipt
  lands in the QC area and only a pass releases the goods into pickable stock
  through an internal transfer that the putaway engine then slots.
* **Unknown-item registration.** A barcode scanned at goods receipt that
  matches no product is captured as a ``custom.wms.product.registration``
  record and turned into a real product after review, instead of blocking the
  receiving operator.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_wms_putaway",
        "stock",
        "product",
        "mail",
    ],
    "capability_tags": ["wms", "quality", "audit-trail", "approval-workflow"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/stock_location_views.xml",
        "views/stock_picking_type_views.xml",
        "views/stock_picking_views.xml",
        "views/product_registration_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
