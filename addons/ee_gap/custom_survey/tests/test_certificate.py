# -*- coding: utf-8 -*-
"""Certificate issuance flow for custom_survey."""

from odoo.tests.common import TransactionCase


class TestCertificate(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Survey = self.env["survey.survey"]
        self.UserInput = self.env["survey.user_input"]

    def _make_survey(self, **overrides):
        vals = {
            "title": "Cert Survey",
            "x_is_certification": True,
            "x_certification_passing_score": 70.0,
            "x_certificate_validity_months": 6,
            "x_certificate_template": (
                "<h1>Cert for {participant_name}</h1>"
                "<p>{survey_title} score {score}</p>"
                "<p>issued {issue_date} valid {valid_until}</p>"
            ),
        }
        vals.update(overrides)
        return self.Survey.create(vals)

    def test_certificate_skipped_when_not_certification(self):
        survey = self._make_survey(x_is_certification=False)
        ui = self.UserInput.create({"survey_id": survey.id})
        from odoo.exceptions import UserError

        with self.assertRaises(UserError):
            survey.action_issue_certificate(ui)

    def test_certificate_skipped_when_below_passing_score(self):
        survey = self._make_survey()
        ui = self.UserInput.create({"survey_id": survey.id})
        # No answers => weighted score is 0%, well below 70.
        result = survey.action_issue_certificate(ui)
        self.assertFalse(result)

    def test_certificate_issued_when_passing(self):
        survey = self._make_survey(x_certification_passing_score=0.0)
        ui = self.UserInput.create({"survey_id": survey.id})
        attachment = survey.action_issue_certificate(ui)
        self.assertTrue(attachment)
        self.assertEqual(attachment.res_model, "survey.user_input")
        self.assertEqual(attachment.res_id, ui.id)
        self.assertIn(attachment.mimetype, ("application/pdf", "text/html"))

    def test_certificate_html_contains_placeholders(self):
        survey = self._make_survey()
        ui = self.UserInput.create({"survey_id": survey.id})
        html = survey._certificate_render_html(ui)
        self.assertIn("Cert for", html)
        self.assertIn("Cert Survey", html)
