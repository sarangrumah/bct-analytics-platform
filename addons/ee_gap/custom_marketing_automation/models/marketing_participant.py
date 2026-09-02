# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields, models


PARTICIPANT_STATES = [
    ("active", "Active"),
    ("completed", "Completed"),
    ("opted_out", "Opted Out"),
]


class MarketingParticipant(models.Model):
    _name = "marketing.participant"
    _description = "Marketing Campaign Participant"
    _order = "campaign_id, partner_id"
    _inherit = ["pdp.audited.mixin"]

    campaign_id = fields.Many2one("marketing.campaign", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", required=True, index=True)
    state = fields.Selection(PARTICIPANT_STATES, default="active", required=True, index=True)
    current_step_id = fields.Many2one("marketing.campaign.step")
    next_action_at = fields.Datetime(default=fields.Datetime.now)
    completed_at = fields.Datetime(readonly=True)

    _unique_campaign_partner = models.Constraint(
        "unique(campaign_id, partner_id)",
        "Partner already in this campaign.",
    )

    def _pdp_audit_classification(self):
        return "pii"

    def _advance(self):
        """Execute the current step then schedule the next."""
        self.ensure_one()
        step = self.current_step_id
        if not step:
            self._complete()
            return
        # Execute
        if step.kind == "email" and step.mail_template_id:
            step.mail_template_id.sudo().send_mail(self.partner_id.id, force_send=False)
            self._pdp_audit_write(
                "marketing_email_sent",
                self.id,
                {"campaign": self.campaign_id.name, "step": step.name},
            )
        elif step.kind == "tag" and step.partner_category_id:
            self.partner_id.sudo().write(
                {
                    "category_id": [(4, step.partner_category_id.id)],
                }
            )
        # 'wait' = no-op; the next_action_at increment handles the pause

        # Advance pointer
        siblings = self.campaign_id.step_ids.sorted("sequence")
        idx = list(siblings).index(step) if step in siblings else -1
        if idx + 1 >= len(siblings):
            self._complete()
            return
        next_step = siblings[idx + 1]
        delay_hours = next_step.wait_hours if next_step.kind == "wait" else 1.0
        self.write(
            {
                "current_step_id": next_step.id,
                "next_action_at": fields.Datetime.now() + timedelta(hours=delay_hours),
            }
        )

    def _complete(self):
        self.write(
            {
                "state": "completed",
                "completed_at": fields.Datetime.now(),
                "current_step_id": False,
            }
        )

    def action_opt_out(self):
        for rec in self:
            rec.write({"state": "opted_out"})
            rec._pdp_audit_write("marketing_opt_out", rec.id, {"campaign": rec.campaign_id.name})
