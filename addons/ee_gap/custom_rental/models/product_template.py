# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_rentable = fields.Boolean(
        string="Can be Rented",
        help="If checked, this product can be referenced from rental orders and rental pricing tiers will apply.",
    )
    rental_pricing_ids = fields.One2many(
        "custom.rental.pricing",
        "product_template_id",
        string="Rental Pricing",
    )
