# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobWalletMove(models.Model):
    _inherit = "custom.ppob.wallet.move"

    va_topup_id = fields.Many2one(
        comodel_name="custom.ppob.va.topup",
        string="VA Topup",
        ondelete="set null",
        index=True,
    )
