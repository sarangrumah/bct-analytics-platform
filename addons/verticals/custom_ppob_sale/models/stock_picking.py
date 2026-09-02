# -*- coding: utf-8 -*-
"""Declare the transaction back-reference on stock.picking.

Lives here (not in custom_ppob_provider) because this module owns
``custom.ppob.transaction``. The provider module's
``_stock_picking_outgoing`` fills this column when it exists, keeping the
provider module independently installable.
"""

from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    x_custom_ppob_transaction_id = fields.Many2one(
        comodel_name="custom.ppob.transaction",
        string="Source PPOB Transaction",
        copy=False,
        index=True,
    )
