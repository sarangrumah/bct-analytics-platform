# -*- coding: utf-8 -*-
"""Add POS mirror move types to custom.ppob.wallet.move."""

from odoo import fields, models


class PpobWalletMove(models.Model):
    _inherit = "custom.ppob.wallet.move"

    type = fields.Selection(
        selection_add=[
            ("pos_sale", "POS Sale (mirror)"),
            ("pos_topup", "POS Top-up (mirror)"),
            ("pos_refund", "POS Refund (mirror)"),
            ("pos_sync", "POS Balance Sync (mirror)"),
        ],
        ondelete={
            "pos_sale": "cascade",
            "pos_topup": "cascade",
            "pos_refund": "cascade",
            "pos_sync": "cascade",
        },
    )
