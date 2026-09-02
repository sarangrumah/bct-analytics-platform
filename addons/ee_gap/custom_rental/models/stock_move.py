# -*- coding: utf-8 -*-
from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    is_loan = fields.Boolean(
        index=True,
        copy=False,
        help="Marks rental loan/cadangan units that must be returned in full and are not invoiced.",
    )
