# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


CAMPAIGN_STATES = [
    ("draft", "Draft"),
    ("running", "Running"),
    ("paused", "Paused"),
    ("completed", "Completed"),
]


class MarketingCampaign(models.Model):
    _name = "marketing.campaign"
    _description = "Marketing Campaign"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(required=True, tracking=True)
    segment_id = fields.Many2one("marketing.segment", required=True)
    state = fields.Selection(CAMPAIGN_STATES, default="draft", required=True, tracking=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)

    step_ids = fields.One2many("marketing.campaign.step", "campaign_id")
    participant_ids = fields.One2many("marketing.participant", "campaign_id")
    participant_count = fields.Integer(compute="_compute_counts")
    completed_count = fields.Integer(compute="_compute_counts")

    require_marketing_consent = fields.Boolean(
        default=True,
        help="Skip participants without a valid 'marketing' consent (custom_pdp_consent).",
    )
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _compute_counts(self):
        for rec in self:
            rec.participant_count = len(rec.participant_ids)
            rec.completed_count = len(rec.participant_ids.filtered(lambda p: p.state == "completed"))

    def action_start(self):
        for rec in self:
            if rec.state == "running":
                continue
            if not rec.step_ids:
                raise UserError(_("Campaign needs at least one step."))
            partners = rec.segment_id.resolve_partners()
            if rec.require_marketing_consent and "pdp.consent" in self.env:
                consent_purpose = self.env.ref(
                    "custom_pdp_consent.consent_purpose_marketing",
                    raise_if_not_found=False,
                )
                if consent_purpose:
                    valid = (
                        self.env["pdp.consent"]
                        .sudo()
                        .search(
                            [
                                ("partner_id", "in", partners.ids),
                                ("purpose_id", "=", consent_purpose.id),
                                ("withdrawn_at", "=", False),
                            ]
                        )
                        .mapped("partner_id")
                    )
                    partners = valid
            Participant = self.env["marketing.participant"].sudo()
            first_step = rec.step_ids.sorted("sequence")[:1]
            for p in partners:
                Participant.create(
                    {
                        "campaign_id": rec.id,
                        "partner_id": p.id,
                        "current_step_id": first_step.id if first_step else False,
                        "state": "active",
                    }
                )
            rec.write({"state": "running", "started_at": fields.Datetime.now()})

    def action_pause(self):
        self.write({"state": "paused"})

    def action_resume(self):
        self.write({"state": "running"})

    def action_complete(self):
        self.write({"state": "completed", "completed_at": fields.Datetime.now()})

    # ----- Cron -----

    @api.model
    def _cron_tick(self):
        """Advance every active participant in every running campaign."""
        Participant = self.env["marketing.participant"].sudo()
        active = Participant.search(
            [
                ("state", "=", "active"),
                ("campaign_id.state", "=", "running"),
                ("next_action_at", "<=", fields.Datetime.now()),
            ]
        )
        for p in active:
            try:
                p._advance()
            except Exception:
                import logging

                logging.getLogger(__name__).exception("participant %s advance failed", p.id)
