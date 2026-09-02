# -*- coding: utf-8 -*-
{
    "name": "Custom Retail Import — POS bridge",
    "summary": "Book imported POS sales/returns with the source file's own tax, discount and return accounts",
    "description": """
Custom Retail Import — POS bridge
=================================

Auto-installed whenever both ``custom_retail_import`` and ``point_of_sale`` are
present. Kept separate because ``custom_retail_import`` deliberately does **not**
depend on ``point_of_sale`` — the tenant runs the importer without POS,
and a hard dependency would force-install the POS application there.

What it does
------------
* Adds ``ri_src_net`` / ``ri_src_tax`` / ``ri_src_discount`` / ``ri_is_return`` to
  ``pos.order.line``, populated by the X24DN and X48 importers from the source
  workbook's own columns.
* Overrides ``pos.order.line._prepare_base_line_for_taxes_computation`` so that
  POS session close books **exactly** those amounts:

  - The source file truncates net to whole rupiah per line
    (``net = trunc(total/1.11)``, ``tax = total - net``) while Odoo rounds tax
    globally per order. Feeding the file's figures through the tax engine's
    ``manual_tax_amounts`` hook removes the resulting per-line 1-rupiah drift.
  - Customer-return lines are re-pointed from ``Gross Sales-<category>`` to
    ``Sales Return-<category>`` so returns are reported on their own COA.
* Appends the source ``NET DISCOUNT AMOUNT`` reclass (Dr ``Sales Discount-<cat>`` /
  Cr ``Gross Sales-<cat>``) to the store's own POS closing entry while it is still
  draft, instead of posting a separate summary journal that no store could tie to.
* Keeps the source workbook's descriptive columns on the posted records: the
  cashier (``ri_staff_id`` / ``ri_staff_name``), the four discount slots folded
  into ``ri_discount_type`` / ``ri_discount_code`` / ``ri_discount_description``,
  and the transaction's member / notes / Omni order id on ``pos.order``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Inventory/Retail",
    "version": "19.0.0.4.0",
    "license": "LGPL-3",
    "depends": [
        "custom_retail_import",
        "point_of_sale",
    ],
    "capability_tags": ["data-import", "retail", "pos", "accounting"],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": True,
}
