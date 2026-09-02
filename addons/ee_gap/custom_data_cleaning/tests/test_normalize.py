# -*- coding: utf-8 -*-
"""Tests for the normalization helpers."""

from odoo.tests.common import TransactionCase, tagged

from ..models.custom_dedup_rule import (
    _normalize_phone_id,
    _validate_nik,
    _is_valid_phone_id_format,
)


@tagged("post_install", "-at_install", "custom_data_cleaning")
class TestNormalizeHelpers(TransactionCase):
    def test_normalize_phone_helper(self):
        # All four input forms collapse to the same canonical output
        self.assertEqual(_normalize_phone_id("081234567890"), "+6281234567890")
        self.assertEqual(_normalize_phone_id("+62 812 3456 7890"), "+6281234567890")
        self.assertEqual(_normalize_phone_id("62-812-3456-7890"), "+6281234567890")
        self.assertEqual(_normalize_phone_id("0062812 3456 7890"), "+6281234567890")
        # Empty stays empty
        self.assertEqual(_normalize_phone_id(""), "")
        self.assertIsNone(_normalize_phone_id(None))

    def test_validate_nik_helper(self):
        self.assertTrue(_validate_nik("1234567890123456"))
        self.assertFalse(_validate_nik("12345"))
        self.assertFalse(_validate_nik("abcdefghijklmnop"))
        self.assertFalse(_validate_nik(""))
        self.assertFalse(_validate_nik(None))

    def test_is_valid_phone_format(self):
        self.assertTrue(_is_valid_phone_id_format("+6281234567890"))
        self.assertFalse(_is_valid_phone_id_format("081234567890"))
        self.assertFalse(_is_valid_phone_id_format(""))

    def test_normalize_wizard_phones(self):
        Partner = self.env["res.partner"]
        Wizard = self.env["custom.dedup.normalize.wizard"]
        p = Partner.create({"name": "Norm Wiz Test", "phone": "081299911122"})
        n = Wizard.action_normalize_phones_id("res.partner", "phone")
        self.assertGreaterEqual(n, 1)
        p.invalidate_recordset(["phone"])
        self.assertEqual(p.phone, "+6281299911122")
