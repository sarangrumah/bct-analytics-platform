# -*- coding: utf-8 -*-
from odoo import fields, models


class ReferralPosition(models.Model):
    _name = "referral.position"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Open Position (Referral)"
    _order = "create_date desc"

    name = fields.Char(required=True)
    department_id = fields.Many2one("hr.department")
    job_id = fields.Many2one("hr.job")
    description = fields.Html()
    reward_amount = fields.Monetary(
        currency_field="currency_id", help="Bonus paid to the referrer when candidate is hired."
    )
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    state = fields.Selection(
        [("open", "Open"), ("on_hold", "On Hold"), ("closed", "Closed")],
        default="open",
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    active = fields.Boolean(default=True)
    candidate_count = fields.Integer(compute="_compute_candidate_count")

    def _compute_candidate_count(self):
        C = self.env["referral.candidate"].sudo()
        for rec in self:
            rec.candidate_count = C.search_count([("position_id", "=", rec.id)])
