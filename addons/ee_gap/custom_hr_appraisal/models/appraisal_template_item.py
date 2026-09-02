# -*- coding: utf-8 -*-
from odoo import fields, models


class AppraisalTemplateItem(models.Model):
    _name = "appraisal.template.item"
    _description = "Appraisal Template Item"
    _order = "template_id, sequence"

    template_id = fields.Many2one("appraisal.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    competency = fields.Char(help="Competency area, e.g. 'Communication', 'Technical'")
    weight = fields.Float(default=1.0)
    description = fields.Text()
