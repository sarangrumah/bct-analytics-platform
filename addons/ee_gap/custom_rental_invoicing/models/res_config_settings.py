# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rental_invoicing_auto_invoice_on_return = fields.Boolean(
        string="Auto-invoice on Rental Return",
        config_parameter="custom_rental_invoicing.auto_invoice_on_return",
        help="If enabled, returning a rental order automatically generates a draft invoice.",
    )
