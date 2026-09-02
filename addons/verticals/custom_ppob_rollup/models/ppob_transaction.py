# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobTransaction(models.Model):
    _inherit = "custom.ppob.transaction"

    x_custom_ppob_rollup_so_id = fields.Many2one(
        comodel_name="sale.order",
        string="Rollup SO",
        readonly=True,
        copy=False,
        index=True,
        help="The daily aggregated sale.order this transaction was rolled up into.",
    )
