# -*- coding: utf-8 -*-
"""Mileage tracking: product detection + rate-based total compute."""

from __future__ import annotations

from odoo.tests.common import TransactionCase


class TestMileage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Expense = cls.env["hr.expense"]
        cls.employee = cls.env["hr.employee"].create({"name": "Test Driver"})
        cls.mileage_product = cls.env.ref("custom_expenses.product_expense_mileage")
        cls.other_product = cls.env["product.product"].create(
            {
                "name": "Generic Travel",
                "default_code": "TRAVEL-GEN",
                "can_be_expensed": True,
            }
        )

    def test_default_mileage_rate_from_config(self):
        rate = self.Expense._default_mileage_rate()
        self.assertEqual(rate, 5000.0)

    def test_default_mileage_rate_respects_override(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_expenses.id_mileage_rate", "7500")
        self.assertEqual(self.Expense._default_mileage_rate(), 7500.0)
        # restore
        self.env["ir.config_parameter"].sudo().set_param("custom_expenses.id_mileage_rate", "5000")

    def test_is_mileage_detected_from_product(self):
        exp = self.Expense.create(
            {
                "name": "Drive to client",
                "employee_id": self.employee.id,
                "product_id": self.mileage_product.id,
                "total_amount": 0.0,
            }
        )
        self.assertTrue(exp.x_is_mileage)

    def test_non_mileage_product_not_flagged(self):
        exp = self.Expense.create(
            {
                "name": "Hotel",
                "employee_id": self.employee.id,
                "product_id": self.other_product.id,
                "total_amount": 100.0,
            }
        )
        self.assertFalse(exp.x_is_mileage)

    def test_mileage_total_computed_on_create(self):
        exp = self.Expense.create(
            {
                "name": "Drive to client",
                "employee_id": self.employee.id,
                "product_id": self.mileage_product.id,
                "x_mileage_km": 42.0,
                "x_mileage_rate": 5000.0,
            }
        )
        # create-time helper sets total when km+rate provided and no total
        self.assertAlmostEqual(exp.total_amount, 42.0 * 5000.0, places=2)

    def test_mileage_total_recomputed_on_write(self):
        exp = self.Expense.create(
            {
                "name": "Drive to client",
                "employee_id": self.employee.id,
                "product_id": self.mileage_product.id,
                "x_mileage_km": 10.0,
                "x_mileage_rate": 5000.0,
            }
        )
        exp.write({"x_mileage_km": 20.0})
        self.assertAlmostEqual(exp.total_amount, 20.0 * 5000.0, places=2)
