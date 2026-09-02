# -*- coding: utf-8 -*-
"""Happy-path tests for x_predictive_score on crm.lead."""

from __future__ import annotations

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPredictiveScore(TransactionCase):
    def test_empty_lead_has_baseline_score(self):
        lead = self.env["crm.lead"].create({"name": "Bare lead"})
        # baseline 30 + nothing filled
        self.assertEqual(lead.x_predictive_score, 30.0)

    def test_each_filled_field_adds_ten(self):
        country = self.env.ref("base.id")
        partner = self.env["res.partner"].create({"name": "ACME Indonesia"})
        source = self.env["utm.source"].create({"name": "TestSource"})
        medium = self.env["utm.medium"].create({"name": "TestMedium"})
        lead = self.env["crm.lead"].create(
            {
                "name": "Rich lead",
                "email_from": "lead@example.com",
                "phone": "+6281234567890",
                "partner_id": partner.id,
                "source_id": source.id,
                "medium_id": medium.id,
                "country_id": country.id,
            }
        )
        # 30 + 10*6 = 90 (no winrate boost yet, the source has no historical wins)
        self.assertEqual(lead.x_predictive_score, 90.0)

    def test_score_clamped_to_100(self):
        country = self.env.ref("base.id")
        partner = self.env["res.partner"].create({"name": "Hot Co"})
        source = self.env["utm.source"].create({"name": "HotSource"})
        medium = self.env["utm.medium"].create({"name": "HotMedium"})
        # Seed historical wins for the source (winrate > 50%)
        for _ in range(3):
            self.env["crm.lead"].create(
                {
                    "name": "won lead",
                    "source_id": source.id,
                    "won_status": "won",
                    "probability": 100.0,
                }
            )
        lead = self.env["crm.lead"].create(
            {
                "name": "Hot lead",
                "email_from": "vip@example.com",
                "phone": "+62811111",
                "partner_id": partner.id,
                "source_id": source.id,
                "medium_id": medium.id,
                "country_id": country.id,
            }
        )
        # 30 + 60 + 20 winrate boost = 110 → clamped to 100
        self.assertEqual(lead.x_predictive_score, 100.0)
