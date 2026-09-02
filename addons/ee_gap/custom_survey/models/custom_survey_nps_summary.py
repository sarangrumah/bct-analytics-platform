# -*- coding: utf-8 -*-
import base64
import csv
import io

from odoo import _, api, fields, models


class CustomSurveyNpsSummary(models.Model):
    _name = "custom.survey.nps.summary"
    _description = "Survey NPS Summary"
    _inherit = ["mail.thread"]
    _order = "date_to desc, id desc"

    name = fields.Char(
        string="Reference",
        compute="_compute_name",
        store=True,
    )
    survey_id = fields.Many2one(
        "survey.survey",
        string="Survey",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    date_from = fields.Date(string="Date From", tracking=True)
    date_to = fields.Date(string="Date To", tracking=True)
    promoter_count = fields.Integer(
        string="Promoters",
        compute="_compute_nps",
        store=True,
    )
    passive_count = fields.Integer(
        string="Passives",
        compute="_compute_nps",
        store=True,
    )
    detractor_count = fields.Integer(
        string="Detractors",
        compute="_compute_nps",
        store=True,
    )
    response_count = fields.Integer(
        string="Responses",
        compute="_compute_nps",
        store=True,
    )
    nps_score = fields.Float(
        string="NPS Score",
        compute="_compute_nps",
        store=True,
        help="(Promoter% - Detractor%) * 100. Range: -100 .. +100.",
    )
    notes = fields.Html(string="Notes")

    @api.depends("survey_id", "survey_id.title", "date_from", "date_to")
    def _compute_name(self):
        for rec in self:
            title = rec.survey_id.title or "Survey"
            if rec.date_from and rec.date_to:
                rec.name = "%s (%s -> %s)" % (title, rec.date_from, rec.date_to)
            elif rec.date_to:
                rec.name = "%s (until %s)" % (title, rec.date_to)
            else:
                rec.name = title

    @api.depends(
        "survey_id",
        "survey_id.x_nps_question_id",
        "date_from",
        "date_to",
    )
    def _compute_nps(self):
        UserInputLine = self.env["survey.user_input.line"].sudo()
        for rec in self:
            promoters = passives = detractors = 0
            question = rec.survey_id.x_nps_question_id
            if question:
                domain = [
                    ("survey_id", "=", rec.survey_id.id),
                    ("question_id", "=", question.id),
                ]
                if rec.date_from:
                    domain.append(("create_date", ">=", rec.date_from))
                if rec.date_to:
                    domain.append(("create_date", "<=", rec.date_to))
                lines = UserInputLine.search(domain)
                for line in lines:
                    # Standard survey numeric values land on value_numerical_box
                    # (or answer_score for scored questions). Pick whichever is set.
                    raw = (
                        line.value_numerical_box
                        if "value_numerical_box" in line._fields and line.value_numerical_box is not False
                        else None
                    )
                    if raw is None and "answer_score" in line._fields:
                        raw = line.answer_score
                    try:
                        score = float(raw) if raw is not None else None
                    except (TypeError, ValueError):
                        score = None
                    if score is None:
                        continue
                    if score >= 9:
                        promoters += 1
                    elif score >= 7:
                        passives += 1
                    elif score >= 0:
                        detractors += 1
            total = promoters + passives + detractors
            rec.promoter_count = promoters
            rec.passive_count = passives
            rec.detractor_count = detractors
            rec.response_count = total
            if total:
                rec.nps_score = ((promoters - detractors) / total) * 100.0
            else:
                rec.nps_score = 0.0

    def action_export_csv(self):
        """Build a CSV attachment of the selected summaries and return a download action."""
        if not self:
            return False
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "Reference",
                "Survey",
                "Date From",
                "Date To",
                "Promoters",
                "Passives",
                "Detractors",
                "Responses",
                "NPS Score",
            ]
        )
        for rec in self:
            writer.writerow(
                [
                    rec.name or "",
                    rec.survey_id.title or "",
                    rec.date_from or "",
                    rec.date_to or "",
                    rec.promoter_count,
                    rec.passive_count,
                    rec.detractor_count,
                    rec.response_count,
                    "%.2f" % (rec.nps_score or 0.0),
                ]
            )
        # utf-8-sig, not utf-8: the BOM tells Excel the file is UTF-8 instead of
        # letting it fall back to the system codepage and render mojibake.
        data = buf.getvalue().encode("utf-8-sig")
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "nps_summary_export.csv",
                    "type": "binary",
                    "datas": base64.b64encode(data),
                    "mimetype": "text/csv",
                    "res_model": self._name,
                    "res_id": self[0].id if len(self) == 1 else 0,
                }
            )
        )
        if hasattr(self, "message_post"):
            for rec in self:
                rec.message_post(body=_("NPS summary CSV exported."))
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }
