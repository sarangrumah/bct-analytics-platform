# -*- coding: utf-8 -*-
from odoo import fields, models


class AppraisalTemplate(models.Model):
    _name = "appraisal.template"
    _description = "Appraisal Template"
    _order = "name"

    name = fields.Char(required=True)
    description = fields.Text()
    item_ids = fields.One2many("appraisal.template.item", "template_id", string="Items")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
