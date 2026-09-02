# -*- coding: utf-8 -*-
"""Corporate card: payment_mode auto-set + PAN validation."""

from __future__ import annotations

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestCorporateCard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Card = cls.env["custom.expense.corporate.card"]
        cls.Expense = cls.env["hr.expense"]
        cls.employee = cls.env["hr.employee"].create({"name": "Test Cardholder"})
        cls.journal = cls.env["account.journal"].search([("type", "in", ("bank", "cash"))], limit=1)
        if not cls.journal:
            cls.journal = cls.env["account.journal"].create(
                {
                    "name": "Test Bank",
                    "type": "bank",
                    "code": "TBNK",
                }
            )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Meals",
                "default_code": "MEAL",
                "can_be_expensed": True,
            }
        )

    def test_card_create_masked(self):
        card = self.Card.create(
            {
                "name": "BCA Corp",
                "masked_number": "**** **** **** 1234",
                "employee_id": self.employee.id,
                "bank_journal_id": self.journal.id,
            }
        )
        self.assertEqual(card.expense_count, 0)

    def test_card_rejects_full_pan(self):
        with self.assertRaises(ValidationError):
            self.Card.create(
                {
                    "name": "Bad Card",
                    "masked_number": "4111 1111 1111 1111",
                    "employee_id": self.employee.id,
                    "bank_journal_id": self.journal.id,
                }
            )

    def test_expense_with_card_forces_company_account(self):
        card = self.Card.create(
            {
                "name": "BCA Corp",
                "masked_number": "**** **** **** 9999",
                "employee_id": self.employee.id,
                "bank_journal_id": self.journal.id,
            }
        )
        exp = self.Expense.create(
            {
                "name": "Client lunch",
                "employee_id": self.employee.id,
                "product_id": self.product.id,
                "total_amount": 150000.0,
                "x_corporate_card_id": card.id,
            }
        )
        # payment_mode auto-coerced to company_account by create()
        if hasattr(exp, "payment_mode"):
            self.assertEqual(exp.payment_mode, "company_account")
        self.assertEqual(exp.x_corporate_card_id, card)

    def test_corporate_card_blocks_reimbursement(self):
        card = self.Card.create(
            {
                "name": "BCA Corp",
                "masked_number": "**** **** **** 5555",
                "employee_id": self.employee.id,
                "bank_journal_id": self.journal.id,
            }
        )
        exp = self.Expense.create(
            {
                "name": "Client lunch",
                "employee_id": self.employee.id,
                "product_id": self.product.id,
                "total_amount": 150000.0,
                "x_corporate_card_id": card.id,
            }
        )
        # Reimbursement must short-circuit when card present
        result = exp.action_register_reimbursement_payment()
        self.assertFalse(result)
