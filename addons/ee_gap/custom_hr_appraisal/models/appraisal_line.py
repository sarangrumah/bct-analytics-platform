# -*- coding: utf-8 -*-
from odoo import fields, models


class AppraisalLine(models.Model):
    _name = "appraisal.line"
    _description = "Appraisal Line"
    _order = "appraisal_id, id"

    appraisal_id = fields.Many2one("appraisal.appraisal", required=True, ondelete="cascade", index=True)
    template_item_id = fields.Many2one("appraisal.template.item")
    name = fields.Char(required=True)
    competency = fields.Char()
    weight = fields.Float(default=1.0)
    score_employee = fields.Integer(
        help="Self-score 1-5",
    )
    score_manager = fields.Integer(
        help="Manager score 1-5",
    )
    comment_employee = fields.Text()
    comment_manager = fields.Text()
