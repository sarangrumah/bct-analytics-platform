# -*- coding: utf-8 -*-
"""Add the 'oracle_sync' move type used by the balance-mirror cron."""

from odoo import fields, models


class PpobWalletMove(models.Model):
    _inherit = "custom.ppob.wallet.move"

    type = fields.Selection(
        selection_add=[("oracle_sync", "Oracle Balance Sync")],
        ondelete={"oracle_sync": "cascade"},
    )
