# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PpobCommissionAccrual(models.Model):
    _name = "custom.ppob.commission.accrual"
    _description = "PPOB Commission Accrual"
    _order = "date desc, id desc"

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.ppob.commission.accrual") or "/",
    )
    transaction_id = fields.Many2one("custom.ppob.transaction", required=True, ondelete="cascade", index=True)
    rule_id = fields.Many2one("custom.ppob.commission.rule", required=True, ondelete="restrict")
    scope = fields.Selection(related="rule_id.scope", store=True)
    partner_id = fields.Many2one("res.partner", required=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    amount = fields.Monetary(currency_field="currency_id", required=True)
    state = fields.Selection(
        selection=[
            ("accrued", "Accrued"),
            ("invoiced", "Invoiced"),
            ("settled", "Settled"),
            ("cancelled", "Cancelled"),
        ],
        default="accrued",
        required=True,
    )
    accrual_move_id = fields.Many2one("account.move", string="Accrual JE", readonly=True)
    settlement_move_id = fields.Many2one("account.move", string="Settlement JE", readonly=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    def _acc(self, role):
        return self.env["custom.ppob.account.mapping"]._get_account(role, self.company_id)

    def _post_accrual(self):
        """Post the accrual journal entry.
        provider_to_us: Dr CommRec / Cr CommIncome
        us_to_mitra   : Dr RebateExp / Cr CommPayable-Mitra
        """
        self.ensure_one()
        if self.accrual_move_id:
            return
        if self.scope == "provider_to_us":
            debit = self._acc("commission_receivable")
            credit = self._acc("commission_income")
        else:
            debit = self._acc("mitra_rebate_expense")
            credit = self._acc("commission_payable_mitra")
        if not (debit and credit):
            raise UserError(
                _("Commission accrual accounts not mapped - check the PPOB account mapping (commission_* roles).")
            )
        journal = self.env.ref("custom_ppob_wallet.journal_ppob_sale", raise_if_not_found=False)
        if not journal:
            raise UserError(_("PPOB Sale journal not found."))
        ref = f"Commission {self.name} ({self.transaction_id.name})"
        move = self.env["account.move"].create(
            {
                "journal_id": journal.id,
                "move_type": "entry",
                "ref": ref,
                "partner_id": self.partner_id.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": debit.id,
                            "partner_id": self.partner_id.id,
                            "name": ref,
                            "debit": self.amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": credit.id,
                            "partner_id": self.partner_id.id,
                            "name": ref,
                            "debit": 0.0,
                            "credit": self.amount,
                        },
                    ),
                ],
            }
        )
        move.action_post()
        self.accrual_move_id = move.id
