# -*- coding: utf-8 -*-
"""End-to-end payslip compute — TER method (Jun) + Annual recon (Dec) + BPJS + THR."""

from __future__ import annotations

from odoo.tests import tagged

from .common import PayrollIDCommon


@tagged("post_install", "-at_install")
class TestPayslipCompute(PayrollIDCommon):
    def test_ter_method_used_for_non_december(self):
        slip = self._make_slip(self.emp_tk0, 10_000_000, month=6)
        slip.action_compute()
        self.assertEqual(slip.calc_method_used, "ter")
        self.assertEqual(slip.ter_category_used, "A")
        # TER A bracket for 10M: 9,650,001-10,050,000 → 2.0%
        self.assertAlmostEqual(slip.ter_rate_used, 2.0, places=4)
        # PPh = 10M × 2% = 200,000
        self.assertAlmostEqual(slip.pph21, 200_000, places=0)

    def test_december_uses_annual_reconciliation(self):
        slip = self._make_slip(self.emp_tk0, 10_000_000, month=12)
        slip.action_compute()
        self.assertEqual(slip.calc_method_used, "annual_recon")
        # Annualised: 120M gross/year, biaya jabatan 6M cap, JHT 2%×120M=2.4M,
        # JP 1% × cap (10.0423M × 12 = 120.5M; capped to 10.0423M, so JP = 100,423×12),
        # PTKP TK/0 = 54M.
        # Should yield a nonzero positive PPh.
        self.assertGreater(slip.pph21, 0)

    def test_bpjs_kesehatan_capped(self):
        slip = self._make_slip(self.emp_tk0, 20_000_000, month=6)
        slip.action_compute()
        # Ceiling 12M, employee 1%, company 4% → 120,000 / 480,000
        self.assertAlmostEqual(slip.bpjs_kesehatan_emp, 120_000, places=0)
        self.assertAlmostEqual(slip.bpjs_kesehatan_company, 480_000, places=0)

    def test_bpjs_jp_capped(self):
        slip = self._make_slip(self.emp_tk0, 20_000_000, month=6)
        slip.action_compute()
        # JP ceiling 10,042,300; employee 1% → 100,423
        self.assertAlmostEqual(slip.bpjs_jp_emp, 100_423, places=0)

    def test_take_home_pay_equals_gross_minus_deductions(self):
        slip = self._make_slip(self.emp_tk0, 10_000_000, month=6)
        slip.action_compute()
        expected_thp = (
            slip.gross_salary
            + (slip.tunjangan_jabatan or 0)
            + (slip.tunjangan_lain or 0)
            - slip.bpjs_kesehatan_emp
            - slip.bpjs_jht_emp
            - slip.bpjs_jp_emp
            - slip.pph21
        )
        self.assertAlmostEqual(slip.take_home_pay, expected_thp, places=2)

    def test_ter_category_b_employee_uses_b_table(self):
        slip = self._make_slip(self.emp_k1, 10_000_000, month=6)
        slip.action_compute()
        self.assertEqual(slip.ter_category_used, "B")
        # TER B for 10M: 9,200,001-10,900,000 → 1.5%
        self.assertAlmostEqual(slip.ter_rate_used, 1.5, places=4)

    def test_employment_type_bukan_pegawai_skips_ter(self):
        self.emp_tk0.x_custom_employment_type = "bukan_pegawai"
        slip = self._make_slip(self.emp_tk0, 10_000_000, month=6)
        slip.action_compute()
        # Should fall through to annualised even for non-December
        self.assertEqual(slip.calc_method_used, "annualised")

    def test_thr_uses_annual_recon(self):
        slip = self._make_slip(self.emp_tk0, 8_000_000, month=6, is_thr=True)
        slip.action_compute()
        self.assertEqual(slip.calc_method_used, "annual_recon")

    def test_approve_creates_bupot_draft(self):
        slip = self._make_slip(self.emp_tk0, 10_000_000, month=6)
        slip.action_compute()
        slip.action_approve()
        self.assertTrue(slip.bupot_id)
        self.assertEqual(slip.bupot_id.state, "draft")
        self.assertEqual(slip.bupot_id.jenis_pph, "21")
        self.assertAlmostEqual(slip.bupot_id.pph_terpotong, slip.pph21, places=0)

    def test_approve_skips_bupot_when_pph_zero(self):
        # Below threshold = 0 PPh, no Bupot
        slip = self._make_slip(self.emp_tk0, 5_000_000, month=6)
        slip.action_compute()
        self.assertEqual(slip.pph21, 0)
        slip.action_approve()
        self.assertFalse(slip.bupot_id)
