# -*- coding: utf-8 -*-
"""Shared fixtures for payroll tests."""

from __future__ import annotations

from odoo.tests.common import TransactionCase


class PayrollIDCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Employee = cls.env["hr.employee"]
        cls.Payslip = cls.env["hr.payslip"]
        cls.Config = cls.env["hr.payroll.config"]
        cls.TER = cls.env["hr.payroll.ter.bracket"]

        # Ensure config exists with defaults
        cls.config = cls.Config.get_default()

        # Test employees — three PTKP statuses (each maps to one TER cat)
        cls.emp_tk0 = cls._make_emp("Karyawan TK0", "TK/0")  # → A
        cls.emp_k1 = cls._make_emp("Karyawan K1", "K/1")  # → B
        cls.emp_k3 = cls._make_emp("Karyawan K3", "K/3")  # → C

    @classmethod
    def _make_emp(cls, name: str, ptkp: str):
        return cls.Employee.create(
            {
                "name": name,
                "x_custom_ptkp_status": ptkp,
                "x_custom_employment_type": "pegawai_tetap",
            }
        )

    def _make_slip(self, employee, gross: float, month: int = 6, year: int = 2026, is_thr: bool = False):
        return self.Payslip.create(
            {
                "employee_id": employee.id,
                "period_year": year,
                "period_month": str(month),
                "gross_salary": gross,
                "is_thr": is_thr,
            }
        )
