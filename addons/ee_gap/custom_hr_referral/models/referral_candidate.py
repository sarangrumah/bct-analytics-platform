# -*- coding: utf-8 -*-
from odoo import fields, models


CANDIDATE_STATES = [
    ("submitted", "Submitted"),
    ("screening", "Screening"),
    ("interviewed", "Interviewed"),
    ("offered", "Offered"),
    ("hired", "Hired"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
]


class ReferralCandidate(models.Model):
    _name = "referral.candidate"
    _description = "Referral Candidate"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(required=True, tracking=True)
    email = fields.Char(required=True, tracking=True)
    phone = fields.Char(tracking=True)
    cv_attachment_id = fields.Many2one("ir.attachment", ondelete="set null")

    position_id = fields.Many2one("referral.position", required=True, index=True)
    referrer_id = fields.Many2one("hr.employee", required=True, index=True, tracking=True)

    state = fields.Selection(CANDIDATE_STATES, default="submitted", required=True, tracking=True, index=True)
    submitted_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    hired_at = fields.Datetime(readonly=True)
    reward_id = fields.Many2one("referral.reward", readonly=True)
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "sensitive_pii"  # candidate PII + reward tied to referrer

    def action_advance(self, target_state: str):
        for rec in self:
            rec.write({"state": target_state})
            rec._pdp_audit_write("referral_state_change", rec.id, {"to": target_state})

    def action_mark_hired(self):
        for rec in self:
            if rec.state == "hired":
                continue
            rec.write({"state": "hired", "hired_at": fields.Datetime.now()})
            rec._materialise_reward()
            rec._pdp_audit_write("referral_hired", rec.id, {"position": rec.position_id.name})

    def _materialise_reward(self):
        self.ensure_one()
        if self.reward_id or not self.position_id.reward_amount:
            return
        Reward = self.env["referral.reward"].sudo()
        self.reward_id = Reward.create(
            {
                "candidate_id": self.id,
                "referrer_id": self.referrer_id.id,
                "amount": self.position_id.reward_amount,
                "currency_id": self.position_id.currency_id.id,
                "state": "pending",
            }
        ).id

    def action_reject(self):
        for rec in self:
            rec.write({"state": "rejected"})

    def action_withdraw(self):
        for rec in self:
            rec.write({"state": "withdrawn"})
