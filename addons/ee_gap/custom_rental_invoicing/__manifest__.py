# -*- coding: utf-8 -*-
{
    "name": "Custom Rental — Invoicing",
    "summary": "Generate account.move (customer invoice) saat rental return — rental fee + late fee + damages",
    "description": """
Extends ``custom_rental`` with proper invoice posting.

Without this module, ``rental.order.total_due`` is a display-only field;
no GL entries are created when the rental is returned. With it:

* New action ``action_create_invoice`` builds a draft ``account.move``
  (out_invoice) with at least one rental-fee line, plus optional late
  fee and damage lines from BAST return.
* Optional auto-post on return (toggle via system parameter
  ``custom_rental_invoicing.auto_invoice_on_return``).
* If ``custom_rental_bom_explosion`` is installed AND the asset has a
  BOM, components are listed as memo lines on the invoice (qty 0,
  price 0) for traceability — the headline charge stays at the bundle
  level.
""",
    "author": "Custom Platform",
    "category": "Sales/Rental",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_rental",
        "account",
    ],
    "capability_tags": ["rental", "invoicing", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "data/ir_config_parameter.xml",
        "views/rental_order_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
