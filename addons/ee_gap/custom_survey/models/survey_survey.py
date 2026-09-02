# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    x_survey_kind = fields.Selection(
        [
            ("employee_pulse", "Employee Pulse"),
            ("customer_nps", "Customer NPS"),
            ("training_feedback", "Training Feedback"),
            ("exit_interview", "Exit Interview"),
            ("other", "Other"),
        ],
        string="Survey Kind",
        default="other",
        help="Classification used by reports and the NPS summary engine.",
    )
    x_target_appraisal_id = fields.Many2one(
        "appraisal.appraisal",
        string="Target Appraisal",
        ondelete="set null",
        help="Optional link to an appraisal record this survey supports (e.g. 360 feedback).",
    )
    x_nps_question_id = fields.Many2one(
        "survey.question",
        string="NPS Score Question",
        ondelete="set null",
        domain="[('survey_id', '=', id)]",
        help="The numeric question (0-10) used to compute NPS for this survey.",
    )

    # --- Certification --------------------------------------------------
    x_is_certification = fields.Boolean(
        string="Issues Certificate",
        default=False,
        help="When enabled, a passing user input can trigger certificate issuance.",
    )
    x_certification_passing_score = fields.Float(
        string="Passing Score (%)",
        default=70.0,
        help="Minimum weighted score percentage to issue a certificate.",
    )
    x_certificate_template = fields.Html(
        string="Certificate Template",
        sanitize=False,
        help=(
            "HTML body for the certificate. Available placeholders: "
            "{participant_name}, {survey_title}, {score}, {issue_date}, {valid_until}."
        ),
    )
    x_certificate_validity_months = fields.Integer(
        string="Certificate Validity (months)",
        default=12,
        help="How many months the certificate is valid after issuance.",
    )

    # --- Anonymity ------------------------------------------------------
    x_anonymity = fields.Selection(
        [
            ("fully_anonymous", "Fully Anonymous"),
            ("partial", "Partial (no public identification)"),
            ("identified", "Identified"),
        ],
        string="Anonymity Mode",
        default="partial",
        help=(
            "fully_anonymous: strip partner_id from answers; "
            "partial: keep partner internally but hide from reports; "
            "identified: full identification."
        ),
    )

    def _certificate_render_html(self, user_input):
        """Render the HTML certificate body for the given user input."""
        self.ensure_one()
        template = self.x_certificate_template or (
            "<h1>Certificate of Completion</h1>"
            "<p>Awarded to <strong>{participant_name}</strong></p>"
            "<p>For successfully completing <em>{survey_title}</em></p>"
            "<p>Score: {score}%</p>"
            "<p>Issued: {issue_date} - Valid until: {valid_until}</p>"
        )
        participant_name = user_input.partner_id.display_name or user_input.email or _("Anonymous")
        issue_date = fields.Date.context_today(self)
        valid_until = fields.Date.add(issue_date, months=self.x_certificate_validity_months or 12)
        score_pct = 0.0
        if "x_weighted_score" in user_input._fields:
            score_pct = user_input.x_weighted_score or 0.0
        elif "scoring_percentage" in user_input._fields:
            score_pct = user_input.scoring_percentage or 0.0
        return template.format(
            participant_name=participant_name,
            survey_title=self.title or "",
            score="%.2f" % score_pct,
            issue_date=issue_date,
            valid_until=valid_until,
        )

    def action_issue_certificate(self, user_input):
        """Issue a certificate attachment + email for a passing user input.

        Returns the created ``ir.attachment`` or ``False`` if not eligible.
        """
        self.ensure_one()
        if not self.x_is_certification:
            raise UserError(_("This survey does not issue certificates."))
        if not user_input or user_input.survey_id != self:
            raise UserError(_("User input does not belong to this survey."))

        # Determine score percentage
        score_pct = 0.0
        if "x_weighted_score" in user_input._fields:
            score_pct = user_input.x_weighted_score or 0.0
        elif "scoring_percentage" in user_input._fields:
            score_pct = user_input.scoring_percentage or 0.0
        if score_pct < (self.x_certification_passing_score or 0.0):
            return False

        html = self._certificate_render_html(user_input)
        report_engine = self.env["ir.actions.report"]
        pdf_content = None
        # Try the Odoo HTML->PDF helper; fall back to storing raw HTML if wkhtmltopdf is unavailable.
        if hasattr(report_engine, "_run_wkhtmltopdf"):
            try:
                pdf_content = report_engine._run_wkhtmltopdf([html])
            except Exception:
                pdf_content = None
        mimetype = "application/pdf"
        ext = "pdf"
        raw = pdf_content
        if not raw:
            raw = html.encode("utf-8")
            mimetype = "text/html"
            ext = "html"

        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "Certificate - %s.%s" % (self.title or "Survey", ext),
                    "type": "binary",
                    "raw": raw,
                    "mimetype": mimetype,
                    "res_model": "survey.user_input",
                    "res_id": user_input.id,
                }
            )
        )

        # Email participant if possible
        email_to = user_input.email or (user_input.partner_id.email if user_input.partner_id else False)
        if email_to:
            mail_values = {
                "subject": _("Your Certificate: %s") % (self.title or ""),
                "body_html": html,
                "email_to": email_to,
                "attachment_ids": [(4, attachment.id)],
                "auto_delete": False,
            }
            self.env["mail.mail"].sudo().create(mail_values).send()

        # Audit trail via mail.thread when available
        if hasattr(self, "message_post"):
            self.message_post(
                body=_("Certificate issued to %s (score %.2f%%).")
                % (
                    user_input.partner_id.display_name or email_to or _("Anonymous"),
                    score_pct,
                ),
            )
        return attachment

    @api.model_create_multi
    def _create_answer(self, *args, **kwargs):
        """Strip partner_id when survey is fully_anonymous.

        Standard ``survey._create_answer`` accepts kwargs / positional usage.
        We intercept after creation to enforce anonymity.
        """
        user_inputs = super()._create_answer(*args, **kwargs)
        for ui in user_inputs:
            if ui.survey_id and ui.survey_id.x_anonymity == "fully_anonymous":
                vals = {}
                if "partner_id" in ui._fields and ui.partner_id:
                    vals["partner_id"] = False
                if "email" in ui._fields and ui.email:
                    vals["email"] = False
                if "nickname" in ui._fields:
                    vals["nickname"] = ""
                if vals:
                    ui.sudo().write(vals)
        return user_inputs
