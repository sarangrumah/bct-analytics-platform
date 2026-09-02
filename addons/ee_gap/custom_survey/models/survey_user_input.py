# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    x_weighted_score = fields.Float(
        string="Weighted Score (%)",
        compute="_compute_weighted_score",
        store=True,
        help=(
            "Sum of (answer_score * question x_score_weight) divided by the sum "
            "of (max_question_score * x_score_weight), expressed as a percentage."
        ),
    )

    @api.depends(
        "user_input_line_ids",
        "user_input_line_ids.answer_score",
        "user_input_line_ids.question_id",
        "user_input_line_ids.question_id.x_score_weight",
        "state",
    )
    def _compute_weighted_score(self):
        for rec in self:
            total_weighted = 0.0
            total_max = 0.0
            # Group answer_score per question to avoid double counting on multi-line questions.
            by_question = {}
            for line in rec.user_input_line_ids:
                q = line.question_id
                if not q:
                    continue
                score = 0.0
                if "answer_score" in line._fields and line.answer_score:
                    score = line.answer_score
                by_question.setdefault(q.id, {"q": q, "score": 0.0})
                by_question[q.id]["score"] += score
            for entry in by_question.values():
                q = entry["q"]
                weight = q.x_score_weight or 0.0
                if weight <= 0.0:
                    continue
                # Best-effort max-score lookup; standard survey stores max on question.suggested_answer_ids
                max_score = 0.0
                if "suggested_answer_ids" in q._fields and q.suggested_answer_ids:
                    pos = [(a.answer_score or 0.0) for a in q.suggested_answer_ids if (a.answer_score or 0.0) > 0]
                    if pos:
                        max_score = max(pos)
                if max_score <= 0.0:
                    # Fall back to 10 for numeric (0..10) scales like NPS
                    max_score = 10.0
                total_weighted += entry["score"] * weight
                total_max += max_score * weight
            rec.x_weighted_score = (total_weighted / total_max * 100.0) if total_max > 0.0 else 0.0

    def _action_done(self):
        """Hook into completion to update linked appraisal + issue certificate."""
        res = super()._action_done()
        for ui in self:
            survey = ui.survey_id
            if not survey:
                continue
            # Certificate issuance
            if survey.x_is_certification:
                try:
                    survey.action_issue_certificate(ui)
                except Exception:
                    # Never block completion on certificate errors
                    if hasattr(survey, "message_post"):
                        survey.message_post(
                            body=_("Certificate issuance failed for user input %s.") % ui.id,
                        )
            # Appraisal integration
            appraisal = survey.x_target_appraisal_id
            if appraisal:
                note = _("Survey '%s' completed by %s (weighted score: %.2f%%).") % (
                    survey.title or "",
                    (ui.partner_id.display_name or ui.email or _("Anonymous")),
                    ui.x_weighted_score or 0.0,
                )
                if hasattr(appraisal, "message_post"):
                    appraisal.sudo().message_post(body=note)
                # Promote draft to self_review when first feedback arrives.
                if appraisal.state == "draft":
                    appraisal.sudo().write({"state": "self_review"})
        return res

    # Keep backward-compat with older survey API name
    def action_complete(self):
        if hasattr(super(), "action_complete"):
            res = super().action_complete()
        else:
            res = self._action_done()
        return res
