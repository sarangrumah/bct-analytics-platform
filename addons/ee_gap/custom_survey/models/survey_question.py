# -*- coding: utf-8 -*-
from odoo import fields, models


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    x_score_weight = fields.Float(
        string="Score Weight",
        default=1.0,
        help=(
            "Multiplier applied to this question's score when computing the weighted total on the survey user input."
        ),
    )
