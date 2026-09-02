# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PRIORITY_SELECTION = [
    ("0", "Very Low"),
    ("1", "Low"),
    ("2", "Normal"),
    ("3", "High"),
]


class CustomMaintenanceTeamSla(models.Model):
    _name = "custom.maintenance.team.sla"
    _description = "Maintenance Team SLA Policy"
    _order = "team_id, priority desc"

    name = fields.Char(
        required=True,
        default=lambda self: self.env._("New SLA Policy"),
    )
    active = fields.Boolean(default=True)
    team_id = fields.Many2one(
        "maintenance.team",
        string="Maintenance Team",
        ondelete="cascade",
        index=True,
        help="Team to which this SLA policy applies. Leave empty for a global default.",
    )
    priority = fields.Selection(
        PRIORITY_SELECTION,
        string="Priority",
        default="2",
        required=True,
        help="Maintenance request priority this policy applies to.",
    )
    response_hours = fields.Integer(
        string="Response Time (hours)",
        default=4,
        required=True,
        help="Hours from request creation until first response is due.",
    )
    resolve_hours = fields.Integer(
        string="Resolution Time (hours)",
        default=24,
        required=True,
        help="Hours from request creation until resolution is due.",
    )
    notes = fields.Text()

    _team_priority_uniq = models.Constraint(
        "unique(team_id, priority)",
        "Only one SLA policy is allowed per team and priority.",
    )

    @api.constrains("response_hours", "resolve_hours")
    def _check_hours(self):
        for rec in self:
            if rec.response_hours < 0 or rec.resolve_hours < 0:
                raise ValidationError(_("SLA hours must be non-negative."))
            if rec.resolve_hours and rec.response_hours and rec.response_hours > rec.resolve_hours:
                raise ValidationError(_("Response time should not exceed resolution time."))

    @api.model
    def _find_for(self, team_id, priority):
        """Return the best-matching SLA policy for the given team and priority."""
        domain_base = [("active", "=", True), ("priority", "=", priority or "2")]
        sla = False
        if team_id:
            sla = self.search(domain_base + [("team_id", "=", team_id)], limit=1)
        if not sla:
            sla = self.search(domain_base + [("team_id", "=", False)], limit=1)
        return sla
