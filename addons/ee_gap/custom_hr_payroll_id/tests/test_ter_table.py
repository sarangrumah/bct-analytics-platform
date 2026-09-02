# -*- coding: utf-8 -*-
"""TER table lookup (PP 58/2023)."""

from __future__ import annotations

from .common import PayrollIDCommon


class TestTerTable(PayrollIDCommon):
    def test_kategori_a_bracket_lookup(self):
        # 7,000,000 falls in TER A bracket 6,750,001-7,500,000 → 1.25%
        rate = self.TER.get_rate("A", 7_000_000)
        self.assertAlmostEqual(rate, 0.0125, places=5)

    def test_kategori_a_zero_when_below_threshold(self):
        # < 5,400,000 → 0%
        self.assertEqual(self.TER.get_rate("A", 5_000_000), 0.0)

    def test_kategori_b_bracket(self):
        # 10,000,000 in cat B: 9,200,001-10,900,000 → 1.5%
        self.assertAlmostEqual(self.TER.get_rate("B", 10_000_000), 0.015, places=5)

    def test_kategori_c_bracket(self):
        # 8,000,000 in cat C: 7,800,001-8,850,000 → 1.0%
        self.assertAlmostEqual(self.TER.get_rate("C", 8_000_000), 0.01, places=5)

    def test_open_ended_top_bracket(self):
        # Very high income hits the open-ended top bracket
        rate_a_top = self.TER.get_rate("A", 5_000_000_000)
        self.assertEqual(rate_a_top, 0.34)
        rate_c_top = self.TER.get_rate("C", 5_000_000_000)
        self.assertEqual(rate_c_top, 0.33)

    def test_category_for_ptkp_mapping(self):
        self.assertEqual(self.TER.category_for_ptkp("TK/0"), "A")
        self.assertEqual(self.TER.category_for_ptkp("K/0"), "A")
        self.assertEqual(self.TER.category_for_ptkp("K/1"), "B")
        self.assertEqual(self.TER.category_for_ptkp("TK/3"), "B")
        self.assertEqual(self.TER.category_for_ptkp("K/3"), "C")
        # Unknown defaults to A
        self.assertEqual(self.TER.category_for_ptkp("UNKNOWN"), "A")

    def test_employee_ter_category_computed(self):
        self.assertEqual(self.emp_tk0.x_custom_ter_category, "A")
        self.assertEqual(self.emp_k1.x_custom_ter_category, "B")
        self.assertEqual(self.emp_k3.x_custom_ter_category, "C")
