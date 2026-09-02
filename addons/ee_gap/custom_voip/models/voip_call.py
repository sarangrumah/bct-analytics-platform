# -*- coding: utf-8 -*-
from odoo import api, fields, models


DIRECTIONS = [("inbound", "Inbound"), ("outbound", "Outbound")]
OUTCOMES = [
    ("answered", "Answered"),
    ("missed", "Missed"),
    ("voicemail", "Voicemail"),
    ("busy", "Busy"),
    ("failed", "Failed"),
]


class VoipCall(models.Model):
    _name = "voip.call"
    _description = "VoIP Call Log"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "started_at desc"

    provider_id = fields.Many2one("voip.provider", required=True)
    direction = fields.Selection(DIRECTIONS, required=True)
    partner_id = fields.Many2one("res.partner", index=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user)
    other_number = fields.Char(required=True, index=True)

    started_at = fields.Datetime(default=fields.Datetime.now, required=True)
    ended_at = fields.Datetime()
    duration_seconds = fields.Integer(compute="_compute_duration", store=True)

    outcome = fields.Selection(OUTCOMES)
    recording_url = fields.Char()
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "pii"  # phone numbers + recording URLs

    @api.depends("started_at", "ended_at")
    def _compute_duration(self):
        for rec in self:
            if rec.started_at and rec.ended_at:
                rec.duration_seconds = int((rec.ended_at - rec.started_at).total_seconds())
            else:
                rec.duration_seconds = 0

    @api.model
    def log_outbound(self, partner_id: int, number: str, user_id: int | None = None):
        """Click-to-call helper — instantiates a placeholder log row."""
        provider = (
            self.env["voip.provider"]
            .sudo()
            .search(
                [("active", "=", True)],
                limit=1,
                order="sequence",
            )
        )
        if not provider:
            return self.browse()
        rec = self.sudo().create(
            {
                "provider_id": provider.id,
                "direction": "outbound",
                "partner_id": partner_id,
                "user_id": user_id or self.env.user.id,
                "other_number": number,
            }
        )
        rec._pdp_audit_write("voip_outbound_started", rec.id, {"partner": partner_id})
        return rec

    def action_mark_answered(self):
        for rec in self:
            rec.write({"outcome": "answered"})

    def action_mark_missed(self):
        for rec in self:
            rec.write({"outcome": "missed", "ended_at": fields.Datetime.now()})

    def action_end(self):
        for rec in self:
            if not rec.ended_at:
                rec.write({"ended_at": fields.Datetime.now()})
            rec._pdp_audit_write(
                "voip_call_ended", rec.id, {"duration_seconds": rec.duration_seconds, "outcome": rec.outcome}
            )
