# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Documents & Labels",
    "summary": "Picking list, packing list, barcode scan sheet and price-tag / product labels",
    "description": """
Warehouse documents & labels.

Adds four QWeb-PDF documents on top of ``stock``:

* **Picking List** — walk-path optimised pick sheet with per-line location QR
  and a tick box for the picker.
* **Packing List** — one block per ``stock.package`` (Odoo 19 renamed
  ``stock.quant.package`` to ``stock.package``) plus a loose/unpacked block,
  with package type dimensions and computed gross weight.
* **Barcode List** — scan sheet with every package and product barcode
  rendered as QR and Code128 side by side.
* **Price Tag / Product Label** — label grid driven by a wizard that can
  explode one label per unit shipped on a transfer.

All report wrappers are self-contained (no ``web.html_container`` /
``web.external_layout``) and carry a ``<main>`` element, which this platform
requires for wkhtmltopdf to render without stalling on an asset callback.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_barcode",
        "stock",
        "product",
    ],
    "capability_tags": ["wms", "barcode-scan", "labels", "reporting", "hht"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "report/picking_list_report.xml",
        "report/packing_list_report.xml",
        "report/barcode_list_report.xml",
        "report/product_label_report.xml",
        "wizard/wms_label_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
