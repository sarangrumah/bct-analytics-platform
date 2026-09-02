# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PpobWalletAdjustWizard(models.TransientModel):
    _name = "custom.ppob.wallet.adjust.wizard"
    _description = "PPOB Wallet Manual Adjustment / Topup"

    wallet_id = fields.Many2one(
        comodel_name="custom.ppob.wallet",
        required=True,
    )
    direction = fields.Selection(
        selection=[
            ("topup", "Topup (credit wallet)"),
            ("adjust_credit", "Credit Adjustment"),
            ("adjust_debit", "Debit Adjustment"),
        ],
        required=True,
        default="topup",
    )
    amount = fields.Monetary(required=True, currency_field="currency_id")
    reason = fields.Char(required=True)
    counterpart_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Counterpart Account",
        required=True,
        help="For topup: bank/cash account debited. For adjustment: income/expense account on the opposite leg.",
    )
    currency_id = fields.Many2one(
        related="wallet_id.currency_id",
        readonly=True,
    )

    def action_apply(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("Amount must be positive."))
        if self.direction in ("topup", "adjust_credit"):
            move_type = "topup" if self.direction == "topup" else "adjust"
            self.wallet_id._atomic_credit(
                amount=self.amount,
                reason=self.reason,
                counterpart_account=self.counterpart_account_id,
                move_type=move_type,
            )
        else:
            self.wallet_id._atomic_debit(
                amount=self.amount,
                reason=self.reason,
                counterpart_account=self.counterpart_account_id,
                move_type="adjust",
            )
        return {"type": "ir.actions.act_window_close"}
