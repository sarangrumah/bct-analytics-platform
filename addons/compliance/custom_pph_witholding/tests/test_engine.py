# -*- coding: utf-8 -*-
"""Tests for the witholding engine."""

from __future__ import annotations

from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_pph_witholding")
class TestWitholdingEngine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["custom.witholding.engine"]
        cls.Rate = cls.env["custom.witholding.rate"]
        cls.Partner = cls.env["res.partner"]
        cls.rate_general = cls.Rate.create(
            {
                "pph_type": "23",
                "service_category": "general",
                "with_npwp_rate": 2.0,
                "without_npwp_rate": 4.0,
                "effective_date_from": "2024-01-01",
            }
        )
        cls.partner_npwp = cls.Partner.create({"name": "PT With NPWP", "vat": "0123456789012345"})
        cls.partner_no_npwp = cls.Partner.create({"name": "Mr. No NPWP", "vat": False})

    def test_01_with_npwp_uses_lower_rate(self):
        result = self.Engine.compute(
            partner=self.partner_npwp,
            amount=10_000_000.0,
            pph_type="23",
            date=date(2026, 5, 19),
            service_category="general",
        )
        self.assertTrue(result["has_npwp"])
        self.assertEqual(result["rate"], 2.0)
        self.assertEqual(result["withheld"], 200_000)
        self.assertAlmostEqual(result["gross_remain"], 9_800_000.0, places=2)
        self.assertEqual(result["applicable_rule_id"], self.rate_general.id)

    def test_02_without_npwp_uses_punitive_rate(self):
        result = self.Engine.compute(
            partner=self.partner_no_npwp,
            amount=10_000_000.0,
            pph_type="23",
            date=date(2026, 5, 19),
            service_category="general",
        )
        self.assertFalse(result["has_npwp"])
        self.assertEqual(result["rate"], 4.0)
        self.assertEqual(result["withheld"], 400_000)

    def test_03_missing_rate_yields_zero_withheld(self):
        result = self.Engine.compute(
            partner=self.partner_npwp,
            amount=5_000_000.0,
            pph_type="26",  # no rule seeded
            date=date(2026, 5, 19),
        )
        self.assertEqual(result["withheld"], 0)
        self.assertEqual(result["rate"], 0.0)
        self.assertFalse(result["applicable_rule_id"])

    def test_04_invalid_npwp_format_treated_as_no_npwp(self):
        bad = self.Partner.create({"name": "Bad NPWP", "vat": "ABC123"})
        result = self.Engine.compute(
            partner=bad,
            amount=1_000_000.0,
            pph_type="23",
            date=date(2026, 5, 19),
            service_category="general",
        )
        self.assertFalse(result["has_npwp"])
        self.assertEqual(result["rate"], 4.0)
        self.assertEqual(result["withheld"], 40_000)

    def test_05_compute_and_log_creates_application_row(self):
        result = self.Engine.compute_and_log(
            partner=self.partner_npwp,
            amount=5_000_000.0,
            pph_type="23",
            date=date(2026, 5, 19),
            service_category="general",
            state="applied",
        )
        application = self.env["custom.witholding.application"].browse(result["application_id"])
        self.assertTrue(application.exists())
        self.assertEqual(application.withheld, 100_000)
        self.assertEqual(application.state, "applied")
        self.assertEqual(application.rule_id, self.rate_general)

    def test_06_rounding_half_up(self):
        # 333,333.33 × 2% = 6,666.6666 → rounds half-up to 6,667
        result = self.Engine.compute(
            partner=self.partner_npwp,
            amount=333_333.33,
            pph_type="23",
            date=date(2026, 5, 19),
            service_category="general",
        )
        self.assertEqual(result["withheld"], 6_667)
