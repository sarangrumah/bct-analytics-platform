# -*- coding: utf-8 -*-
from odoo import _, fields, models


class SlideSlide(models.Model):
    _inherit = "slide.slide"

    x_passing_score = fields.Float(
        string="Passing Score (%)",
        default=70.0,
        help="Minimum percentage required to consider this quiz passed.",
    )

    def check_quiz_pass(self, score):
        """Return True if ``score`` (0..100) meets the passing threshold.

        ``score`` may be supplied either as a raw percentage (0..100) or as a
        fraction (0..1) — both are normalised here.
        """
        self.ensure_one()
        if score is None:
            return False
        try:
            value = float(score)
        except (TypeError, ValueError):
            return False
        if 0.0 <= value <= 1.0:
            value = value * 100.0
        threshold = float(self.x_passing_score or 0.0)
        passed = value >= threshold
        self.message_post(
            body=_("Quiz check: score=%(score).2f%% threshold=%(threshold).2f%% -> %(result)s")
            % {
                "score": value,
                "threshold": threshold,
                "result": _("PASSED") if passed else _("FAILED"),
            }
        )
        return passed
