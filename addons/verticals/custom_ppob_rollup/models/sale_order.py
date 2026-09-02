# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_custom_ppob_is_rollup = fields.Boolean(
        string="PPOB Rollup Document",
        default=False,
        index=True,
        help="Marks sale.orders that are PPOB daily aggregates. The linked "
        "invoice uses a summary journal excluded from the TB.",
    )
    x_custom_ppob_rollup_date = fields.Date(string="Rollup Date", index=True)
    x_custom_ppob_transaction_ids = fields.One2many(
        "custom.ppob.transaction",
        "x_custom_ppob_rollup_so_id",
        string="Rolled-up Transactions",
    )
    x_custom_ppob_transaction_count = fields.Integer(
        compute="_compute_x_custom_ppob_transaction_count",
    )

    def _compute_x_custom_ppob_transaction_count(self):
        for so in self:
            so.x_custom_ppob_transaction_count = len(so.x_custom_ppob_transaction_ids)

    def action_open_rolled_up(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Rolled-up Transactions",
            "res_model": "custom.ppob.transaction",
            "view_mode": "list,form",
            "domain": [("x_custom_ppob_rollup_so_id", "=", self.id)],
        }
