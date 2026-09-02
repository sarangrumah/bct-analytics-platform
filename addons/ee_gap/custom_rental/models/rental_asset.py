# -*- coding: utf-8 -*-
from odoo import fields, models


class RentalAsset(models.Model):
    _name = "rental.asset"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Rental Asset"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    product_id = fields.Many2one("product.product")
    serial_number = fields.Char()
    daily_rate = fields.Monetary(currency_field="currency_id", required=True, default=0.0)
    deposit_amount = fields.Monetary(currency_field="currency_id", default=0.0)
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    state = fields.Selection(
        [("available", "Available"), ("on_rent", "On Rent"), ("maintenance", "Maintenance"), ("retired", "Retired")],
        default="available",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Asset code must be unique.",
    )
