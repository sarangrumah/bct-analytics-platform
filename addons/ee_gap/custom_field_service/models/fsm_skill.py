# -*- coding: utf-8 -*-
from odoo import fields, models


class FSMSkill(models.Model):
    _name = "fsm.skill"
    _description = "Field Service Skill"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    description = fields.Text()
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Skill code must be unique.",
    )
