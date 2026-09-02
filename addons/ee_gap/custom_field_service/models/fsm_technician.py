# -*- coding: utf-8 -*-
from odoo import fields, models


class FSMTechnician(models.Model):
    _name = "fsm.technician"
    _description = "Field Service Technician"
    _order = "name"

    name = fields.Char(required=True)
    user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    employee_id = fields.Many2one("hr.employee")
    phone = fields.Char()
    skill_ids = fields.Many2many(
        "fsm.skill",
        "fsm_technician_skill_rel",
        "technician_id",
        "skill_id",
        string="Skills",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    open_wo_count = fields.Integer(compute="_compute_open_wo_count")

    def _compute_open_wo_count(self):
        WO = self.env["fsm.work.order"].sudo()
        for rec in self:
            rec.open_wo_count = WO.search_count(
                [
                    ("technician_id", "=", rec.id),
                    ("state", "in", ("scheduled", "in_progress", "on_hold")),
                ]
            )
