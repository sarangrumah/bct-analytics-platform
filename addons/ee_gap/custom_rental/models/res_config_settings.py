# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rental_stock_integration = fields.Boolean(
        string="Generate Stock Pickings for Rentals",
        config_parameter="custom_rental.config_stock_integration",
        default=True,
        help="When enabled, confirming a rental order creates an outbound "
        "stock.picking; marking returned creates an inbound picking.",
    )
    rental_default_late_fee_rate = fields.Float(
        string="Default Late Fee Rate (% / day)",
        config_parameter="custom_rental.default_late_fee_rate",
        default=10.0,
    )
