# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobTransaction(models.Model):
    _inherit = "custom.ppob.transaction"

    commission_accrual_ids = fields.One2many(
        "custom.ppob.commission.accrual",
        "transaction_id",
        string="Commission Accruals",
    )

    def _mark_success(self, provider_ref=None, serial_token=None):
        result = super()._mark_success(provider_ref=provider_ref, serial_token=serial_token)
        for txn in self:
            if txn.state == "success":
                txn._accrue_commissions()
        return result

    def _accrue_commissions(self):
        """Evaluate matching rules and create accruals."""
        self.ensure_one()
        Rule = self.env["custom.ppob.commission.rule"]
        Accrual = self.env["custom.ppob.commission.accrual"]
        rules = Rule.search([("active", "=", True)], order="priority asc, id asc")
        for rule in rules:
            if not rule._matches(self):
                continue
            amount = rule._compute_amount(self)
            if amount <= 0:
                continue
            if rule.scope == "provider_to_us":
                partner = self.provider_id.partner_id
            else:
                partner = self.mitra_id
            if not partner:
                continue
            accrual = Accrual.create(
                {
                    "transaction_id": self.id,
                    "rule_id": rule.id,
                    "partner_id": partner.id,
                    "amount": amount,
                }
            )
            accrual._post_accrual()
