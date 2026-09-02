# -*- coding: utf-8 -*-
from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    supplier_batch_ref = fields.Char(
        string="Supplier Batch Ref",
        index=True,
        help="The supplier's own batch/lot reference for this lot, as printed "
        "on the goods or the supplier's delivery note.",
    )
