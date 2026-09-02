# -*- coding: utf-8 -*-
from odoo import fields, models


class KnowledgeTag(models.Model):
    _name = "knowledge.tag"
    _description = "Knowledge Tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        "unique(name)",
        "Tag name must be unique.",
    )
