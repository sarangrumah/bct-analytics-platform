# -*- coding: utf-8 -*-
from odoo import fields, models


class ReferralReward(models.Model):
    _name = "referral.reward"
    _description = "Referral Reward"
    _inherit = ["pdp.audited.mixin"]
    _order = "create_date desc"

    candidate_id = fields.Many2one("referral.candidate", required=True, ondelete="cascade")
    referrer_id = fields.Many2one("hr.employee", required=True, index=True)
    amount = fields.Monetary(currency_field="currency_id", required=True)
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    state = fields.Selection(
        [("pending", "Pending"), ("approved", "Approved"), ("paid", "Paid")],
        default="pending",
        required=True,
    )
    approved_at = fields.Datetime(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "financial"

    def action_approve(self):
        for rec in self:
            rec.write({"state": "approved", "approved_at": fields.Datetime.now()})
            rec._pdp_audit_write("referral_reward_approve", rec.id, {"amount": float(rec.amount)})

    def action_pay(self):
        for rec in self:
            rec.write({"state": "paid", "paid_at": fields.Datetime.now()})
            rec._pdp_audit_write("referral_reward_pay", rec.id, {"amount": float(rec.amount)})
