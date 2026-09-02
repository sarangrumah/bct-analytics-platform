# -*- coding: utf-8 -*-
{
    "name": "Retail Import — X-Store Reconciliation",
    "summary": "Per-transaction reconciliation of the X24DN sales file against what actually posted in Odoo",
    "description": """
Retail Import — X-Store Reconciliation
--
Finance's question after every nightly import is the same: *did everything the
stores rang up actually land in Odoo, and if not, why not?* Answering it used to
mean exporting the POS list, exporting the X-Store file, and diffing them in a
spreadsheet.

This report answers it on screen. One row per source transaction, straight from
the staged X24DN rows, showing:

* the transaction header - store, trading day, register, receipt number, cashier
  and member, so a row can be traced back to a physical receipt;
* what the file said - line count, quantity, gross amount and tax;
* what Odoo booked - the POS order and its total;
* the difference, and a status saying whether it was **posted**, **parked**
  (rejected, with the importer's own reason), or **not found**.

Separate module on purpose. It joins ``pos_order``, so it depends on
``point_of_sale`` - and ``trn_tenant`` runs ``custom_retail_import`` without POS.
Folding this into the shared addon would force a POS install on that tenant.
    """,
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Retail",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_retail_import",
        "custom_accounting_reports",
        "point_of_sale",
        "account",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/retail_import_recon_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
