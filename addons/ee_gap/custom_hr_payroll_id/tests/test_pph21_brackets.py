# -*- coding: utf-8 -*-
"""PPh 21 progressive bracket math (UU HPP 2021)."""

from __future__ import annotations

from ..models.hr_payslip import _compute_pph21
from .common import PayrollIDCommon


class TestPPh21Brackets(PayrollIDCommon):
    def test_zero_income(self):
        self.assertEqual(_compute_pph21(0), 0.0)

    def test_negative_treated_as_zero(self):
        self.assertEqual(_compute_pph21(-1_000_000), 0.0)

    def test_first_bracket_5pct(self):
        # 50M taxable, all in 5% bracket
        self.assertAlmostEqual(_compute_pph21(50_000_000), 2_500_000, places=2)

    def test_first_bracket_boundary(self):
        # Exactly 60M: still 5% on entire amount
        self.assertAlmostEqual(_compute_pph21(60_000_000), 3_000_000, places=2)

    def test_second_bracket_15pct(self):
        # 100M: 60M × 5% + 40M × 15% = 3M + 6M = 9M
        self.assertAlmostEqual(_compute_pph21(100_000_000), 9_000_000, places=2)

    def test_third_bracket_25pct(self):
        # 300M: 60M×5% + 190M×15% + 50M×25% = 3M + 28.5M + 12.5M = 44M
        self.assertAlmostEqual(_compute_pph21(300_000_000), 44_000_000, places=2)

    def test_fourth_bracket_30pct(self):
        # 1B: 60M×5% + 190M×15% + 250M×25% + 500M×30%
        # = 3M + 28.5M + 62.5M + 150M = 244M
        self.assertAlmostEqual(_compute_pph21(1_000_000_000), 244_000_000, places=2)

    def test_top_bracket_35pct(self):
        # 10B: 60M×5% + 190M×15% + 250M×25% + 4.5B×30% + 5B×35%
        # = 3M + 28.5M + 62.5M + 1.35B + 1.75B = 3.194B
        self.assertAlmostEqual(_compute_pph21(10_000_000_000), 3_194_000_000, places=0)
