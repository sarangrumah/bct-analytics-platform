# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobWalletMove(models.Model):
    _inherit = "custom.ppob.wallet.move"

    ppob_transaction_id = fields.Many2one(
        comodel_name="custom.ppob.transaction",
        string="PPOB Transaction",
        ondelete="set null",
        index=True,
    )
