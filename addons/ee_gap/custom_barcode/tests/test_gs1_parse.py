# -*- coding: utf-8 -*-
"""Unit tests for the GS1 Application-Identifier parser on scan.session."""

from odoo.tests.common import TransactionCase


class TestGS1Parse(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env["custom.barcode.scan.session"]

    def test_parse_gtin_only(self):
        # AI 01 = GTIN, 14 digits.
        result = self.Session.parse_gs1("0109501234543213")
        self.assertEqual(result.get("gtin"), "09501234543213")

    def test_parse_gtin_and_lot(self):
        # AI 01 + AI 10 (variable, ended by FNC1 / end of string).
        result = self.Session.parse_gs1("010950123454321310ABC123")
        self.assertEqual(result.get("gtin"), "09501234543213")
        self.assertEqual(result.get("lot"), "ABC123")

    def test_parse_lot_with_fnc1(self):
        # AI 10 then FNC1 separator then AI 17 (expiry).
        raw = "10LOTX\x1d17261231"
        result = self.Session.parse_gs1(raw)
        self.assertEqual(result.get("lot"), "LOTX")
        # 26-12-31 → 2026-12-31
        self.assertEqual(result.get("exp_date"), "2026-12-31")

    def test_parse_weight_kg(self):
        # AI 3103 = net weight kg with 3 decimals; 6 digits follow.
        # 001250 / 10^3 = 1.250 kg
        result = self.Session.parse_gs1("3103001250")
        self.assertAlmostEqual(result.get("weight"), 1.250, places=3)
        self.assertEqual(result.get("weight_unit"), "kg")

    def test_unknown_returns_empty(self):
        # Plain EAN-13, not GS1 — no leading recognised AI.
        result = self.Session.parse_gs1("4006381333931")
        self.assertEqual(result, {})

    def test_empty_input(self):
        self.assertEqual(self.Session.parse_gs1(""), {})
        self.assertEqual(self.Session.parse_gs1(False), {})
