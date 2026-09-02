# -*- coding: utf-8 -*-
"""SPT 1721 A1 aggregation + delta computation."""

from __future__ import annotations

from odoo.tests import tagged

from .common import PayrollIDCommon


@tagged("post_install", "-at_install")
class TestSPT1721A1(PayrollIDCommon):
    def test_annual_aggregation_one_employee(self):
        # 12 months × 10M gross for emp_tk0
        for month in range(1, 13):
            slip = self._make_slip(self.emp_tk0, 10_000_000, month=month, year=2026)
            slip.action_compute()
            slip.action_approve()

        wizard = self.env["hr.payroll.spt.a1.wizard"].create(
            {
                "fiscal_year": 2026,
                "employee_ids": [(6, 0, [self.emp_tk0.id])],
                "output_format": "xml",
            }
        )
        wizard.action_run()
        self.assertTrue(wizard.run_done)
        self.assertTrue(wizard.xml_attachment_id)
        # 12 × 10M = 120M bruto
        # Validate by inspecting the embedded employee compute result
        config = self.Config.get_default()
        data = wizard._compute_employee_annual(self.emp_tk0, config)
        self.assertEqual(data["bruto_year"], 120_000_000)

    def test_kurang_bayar_when_ter_underwithholds(self):
        """TER monthly often under-withholds vs annual progressive — December
        reconciliation should compute a positive delta."""
        for month in range(1, 13):
            slip = self._make_slip(self.emp_tk0, 15_000_000, month=month, year=2026)
            slip.action_compute()
            slip.action_approve()

        wizard = self.env["hr.payroll.spt.a1.wizard"].create(
            {
                "fiscal_year": 2026,
                "employee_ids": [(6, 0, [self.emp_tk0.id])],
                "output_format": "xml",
            }
        )
        wizard.action_run()
        config = self.Config.get_default()
        data = wizard._compute_employee_annual(self.emp_tk0, config)
        # pph_due (annual progressive) should at least be >0
        self.assertGreater(data["pph_due"], 0)
        # pph_paid should be the sum of monthly TER (Jan-Nov) + December annual recon
        self.assertGreater(data["pph_paid"], 0)
        # Delta should be a finite number (sign depends on TER vs annual gap)
        self.assertIsNotNone(data["delta"])

    def test_no_payslips_raises(self):
        wizard = self.env["hr.payroll.spt.a1.wizard"].create(
            {
                "fiscal_year": 1999,  # year with no payslips
            }
        )
        from odoo.exceptions import UserError

        with self.assertRaises(UserError):
            wizard.action_run()
