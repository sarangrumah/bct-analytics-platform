# -*- coding: utf-8 -*-
"""Anonymity-mode stripping of partner_id on survey user inputs."""

from odoo.tests.common import TransactionCase


class TestAnonymity(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Survey = self.env["survey.survey"]
        self.Partner = self.env["res.partner"]
        self.partner = self.Partner.create(
            {
                "name": "Test Respondent",
                "email": "respondent@example.com",
            }
        )

    def test_partial_keeps_partner(self):
        survey = self.Survey.create(
            {
                "title": "Partial Survey",
                "x_anonymity": "partial",
            }
        )
        ui = self.env["survey.user_input"].create(
            {
                "survey_id": survey.id,
                "partner_id": self.partner.id,
            }
        )
        # Partial mode keeps the partner attached.
        self.assertEqual(ui.partner_id, self.partner)

    def test_identified_keeps_partner(self):
        survey = self.Survey.create(
            {
                "title": "Identified Survey",
                "x_anonymity": "identified",
            }
        )
        ui = self.env["survey.user_input"].create(
            {
                "survey_id": survey.id,
                "partner_id": self.partner.id,
            }
        )
        self.assertEqual(ui.partner_id, self.partner)

    def test_fully_anonymous_strips_partner_via_create_answer(self):
        survey = self.Survey.create(
            {
                "title": "Anon Survey",
                "x_anonymity": "fully_anonymous",
            }
        )
        # The override targets _create_answer; call it directly with a partner.
        try:
            user_inputs = survey._create_answer(partner=self.partner)
        except TypeError:
            # Older signature variants — fall back to dict form
            user_inputs = survey._create_answer(partner_id=self.partner.id)
        for ui in user_inputs:
            self.assertFalse(ui.partner_id, "partner_id should be stripped in fully_anonymous mode")
            if "email" in ui._fields:
                self.assertFalse(ui.email)

    def test_default_anonymity_is_partial(self):
        survey = self.Survey.create({"title": "Default Anon"})
        self.assertEqual(survey.x_anonymity, "partial")
