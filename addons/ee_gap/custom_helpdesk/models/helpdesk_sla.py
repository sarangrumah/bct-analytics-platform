# -*- coding: utf-8 -*-
from odoo import fields, models


class HelpdeskSla(models.Model):
    _name = "helpdesk.sla"
    _description = "Helpdesk SLA Policy"
    _order = "priority desc, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    priority = fields.Selection(
        [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Urgent")],
        string="Applies to Priority",
        default="1",
        required=True,
    )
    time_response_hours = fields.Float(
        string="Response Time (hours)",
        default=4.0,
        help="Hours from ticket creation until first response is due.",
    )
    time_resolve_hours = fields.Float(
        string="Resolution Time (hours)",
        default=24.0,
        help="Hours from ticket creation until resolution is due.",
    )
    team_ids = fields.One2many("helpdesk.team", "sla_id", string="Teams")
