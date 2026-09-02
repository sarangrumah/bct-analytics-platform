# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobWalletMove(models.Model):
    _name = "custom.ppob.wallet.move"
    _description = "PPOB Wallet Ledger Line"
    _order = "date desc, id desc"

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.ppob.wallet.move") or "/",
    )
    wallet_id = fields.Many2one(
        comodel_name="custom.ppob.wallet",
        required=True,
        ondelete="restrict",
        index=True,
    )
    partner_id = fields.Many2one(
        related="wallet_id.partner_id",
        store=True,
        readonly=True,
    )
    class_id = fields.Many2one(
        related="wallet_id.class_id",
        store=True,
        readonly=True,
    )
    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    type = fields.Selection(
        selection=[
            ("topup", "Topup"),
            ("sale", "Sale"),
            ("refund", "Refund"),
            ("adjust", "Adjustment"),
            ("commission_payout", "Commission Payout"),
        ],
        required=True,
    )
    amount_signed = fields.Monetary(
        required=True,
        currency_field="currency_id",
        help="Positive for credits (topup, refund), negative for debits (sale, payout).",
    )
    balance_after = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
    )
    ref = fields.Char(string="Reference")
    # ppob_transaction_id and va_topup_id are added by custom_ppob_sale and
    # custom_ppob_va respectively to avoid hard cross-module dependencies.
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        ondelete="restrict",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("reversed", "Reversed"),
        ],
        default="draft",
        required=True,
    )
    currency_id = fields.Many2one(
        related="wallet_id.currency_id",
        store=True,
        readonly=True,
    )

    def action_open_move(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Journal Entry",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }
