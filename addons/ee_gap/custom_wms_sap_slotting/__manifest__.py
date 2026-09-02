# -*- coding: utf-8 -*-
{
    "name": "Custom WMS SAP Slotting",
    "summary": "SAP-style two-dimensional storage search (Lagertyp x Lagerbereich) putaway",
    "description": """
Adds the SAP WM slotting dimensions that ``custom_wms_putaway`` does not model:

* **Storage Type** (SAP Lagertyp) — AC1/AC2/AP1/AP2/FO1/FO2/FL1 in the
  reference configuration — and **Storage Section** (SAP Lagerbereich) —
  BB1/GF1/GO1/LS1/OD1/RU1/SL1/SS1/TR1/GA2 — as first-class records carrying an
  ordered *search sequence* each.
* A ``sap_storage_search`` putaway rule kind that walks the two sequences
  (storage type outer, storage section inner) and slots into the first bin with
  free volume, scoring by how far down each sequence it had to go.

Everything is added by inheritance. ``custom_wms_putaway`` is untouched, so the
shared addon does not need updating on tenant databases that do not use SAP
slotting.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_wms_putaway",
        "stock",
        "product",
    ],
    "capability_tags": ["wms", "slotting", "putaway", "sap"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/custom.wms.storage.type.csv",
        "data/custom.wms.storage.type.search.line.csv",
        "data/custom.wms.storage.section.csv",
        "data/custom.wms.storage.section.search.line.csv",
        "views/wms_storage_type_views.xml",
        "views/wms_storage_section_views.xml",
        "views/stock_location_views.xml",
        "views/product_template_views.xml",
        "views/putaway_rule_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
