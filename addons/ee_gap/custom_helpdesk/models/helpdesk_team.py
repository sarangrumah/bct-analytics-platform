# -*- coding: utf-8 -*-
from odoo import api, fields, models


class HelpdeskTeam(models.Model):
    _name = "helpdesk.team"
    _description = "Helpdesk Team"
    _inherit = ["mail.alias.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    member_ids = fields.Many2many(
        "res.users",
        "helpdesk_team_user_rel",
        "team_id",
        "user_id",
        string="Members",
    )
    default_priority = fields.Selection(
        [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Urgent")],
        default="1",
    )
    sla_id = fields.Many2one("helpdesk.sla", string="Default SLA")
    ticket_ids = fields.One2many("helpdesk.ticket", "team_id", string="Tickets")
    ticket_count = fields.Integer(compute="_compute_ticket_count")

    @api.depends("ticket_ids")
    def _compute_ticket_count(self):
        for rec in self:
            rec.ticket_count = len(rec.ticket_ids)

    # mail.alias.mixin: when an email lands at <alias>@..., create a ticket
    def _alias_get_creation_values(self):
        values = super()._alias_get_creation_values()
        values["alias_model_id"] = self.env["ir.model"]._get("helpdesk.ticket").id
        if self.id:
            values["alias_defaults"] = defaults = {}
            defaults["team_id"] = self.id
            if self.default_priority:
                defaults["priority"] = self.default_priority
        return values
