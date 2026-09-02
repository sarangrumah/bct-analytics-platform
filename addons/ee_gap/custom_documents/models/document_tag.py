# -*- coding: utf-8 -*-
from odoo import fields, models


class DocumentTag(models.Model):
    _name = "document.tag"
    _description = "Document Tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        "unique(name)",
        "Tag name must be unique.",
    )
