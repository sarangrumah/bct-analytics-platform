# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomSpreadsheetTag(models.Model):
    _name = "custom.spreadsheet.tag"
    _description = "Spreadsheet Tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer(default=0)

    _name_uniq = models.Constraint(
        "unique(name)",
        "Tag name must be unique.",
    )
