# -*- coding: utf-8 -*-
"""Product hints used by withholding rule resolution.

Some products (e.g. jasa konsultan) attract specific PPh categories
regardless of partner; flag them at product level so the resolver can
short-circuit.
"""

from __future__ import annotations

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    x_custom_withholding_category_id = fields.Many2one(
        "tax.withholding.category",
        string="PPh Withholding Category",
        help="If set, vendor bill lines on this product default to this PPh "
        "category — useful for jasa konsultan, sewa, royalti, dst.",
    )
