# -*- coding: utf-8 -*-
from odoo import fields, models


class RentalAsset(models.Model):
    _inherit = "rental.asset"

    fixed_asset_id = fields.Many2one(
        comodel_name="custom.fixed.asset",
        string="Fixed Asset",
        index=True,
        ondelete="set null",
        help="Accounting-side fixed asset corresponding to this rental unit.",
    )
