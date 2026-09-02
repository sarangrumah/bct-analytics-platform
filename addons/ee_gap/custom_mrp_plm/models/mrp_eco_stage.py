# -*- coding: utf-8 -*-
from odoo import fields, models


class MrpEcoStage(models.Model):
    _name = "mrp.eco.stage"
    _description = "ECO Stage"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    is_approval = fields.Boolean(
        help="Stage requires manager approval to advance past.",
    )
    is_final = fields.Boolean(
        help="When ECO reaches this stage, the new revision is promoted to active.",
    )
    folded = fields.Boolean(help="Folded in kanban.")
    active = fields.Boolean(default=True)
