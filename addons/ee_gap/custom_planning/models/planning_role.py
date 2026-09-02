# -*- coding: utf-8 -*-
from odoo import fields, models


class PlanningRole(models.Model):
    _name = "planning.role"
    _description = "Planning Role"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer()
    employee_ids = fields.Many2many(
        "hr.employee",
        "planning_role_employee_rel",
        "role_id",
        "employee_id",
        string="Eligible Employees",
    )
    active = fields.Boolean(default=True)
