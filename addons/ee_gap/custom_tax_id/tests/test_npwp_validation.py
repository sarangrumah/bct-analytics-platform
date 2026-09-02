# -*- coding: utf-8 -*-
"""NPWP / NIK / foreign-counterparty flag behaviour."""

from __future__ import annotations

from odoo.exceptions import ValidationError

from .common import TaxIdCommon


class TestNpwpValidation(TaxIdCommon):
    def test_15_digit_npwp_is_valid(self):
        p = self.Partner.create({"name": "Test", "x_custom_npwp": "012345678901234"})
        self.assertEqual(p.x_custom_npwp_status, "valid")
        self.assertTrue(p.x_custom_has_valid_npwp)

    def test_16_digit_npwp_is_valid(self):
        p = self.Partner.create({"name": "Test", "x_custom_npwp": "0123456789012345"})
        self.assertEqual(p.x_custom_npwp_status, "valid")

    def test_invalid_npwp_flagged(self):
        p = self.Partner.create({"name": "Test", "x_custom_npwp": "abc-12345"})
        self.assertEqual(p.x_custom_npwp_status, "invalid")
        self.assertFalse(p.x_custom_has_valid_npwp)

    def test_empty_npwp_is_none(self):
        p = self.Partner.create({"name": "Test"})
        self.assertEqual(p.x_custom_npwp_status, "none")
        self.assertFalse(p.x_custom_has_valid_npwp)

    def test_dotted_npwp_still_valid(self):
        # Real-world: 01.234.567.8-901.234 (15 digits + separators)
        p = self.Partner.create({"name": "Test", "x_custom_npwp": "01.234.567.8-901.234"})
        self.assertEqual(p.x_custom_npwp_status, "valid")

    def test_bad_nik_rejected(self):
        with self.assertRaises(ValidationError):
            self.Partner.create({"name": "Test", "x_custom_nik": "not-16-digits"})

    def test_foreign_counterparty_computed(self):
        self.assertTrue(self.vendor_foreign.x_custom_foreign_counterparty)
        self.assertFalse(self.vendor_npwp.x_custom_foreign_counterparty)
