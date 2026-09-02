# -*- coding: utf-8 -*-
"""Tests for recurring journal entry and payment templates."""

from __future__ import annotations

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRecurringJournalTemplate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Move = cls.env["account.move"]
        cls.Template = cls.env["custom.recurring.journal.template"]
        # Build minimal chart pieces
        cls.account_debit = cls.env["account.account"].create(
            {
                "code": "RECT-DR-01",
                "name": "Recurring Debit",
                "account_type": "expense",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.account_credit = cls.env["account.account"].create(
            {
                "code": "RECT-CR-01",
                "name": "Recurring Credit",
                "account_type": "liability_current",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Recurring Misc",
                "code": "RECMI",
                "type": "general",
                "company_id": cls.company.id,
            }
        )

    def _make_template(self, **overrides):
        vals = {
            "name": "Monthly accrual",
            "company_id": self.company.id,
            "journal_id": self.journal.id,
            "period": "monthly",
            "next_date": date.today() - timedelta(days=1),
            "auto_post": False,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": "Debit",
                        "account_id": self.account_debit.id,
                        "debit": 100.0,
                        "credit": 0.0,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "name": "Credit",
                        "account_id": self.account_credit.id,
                        "debit": 0.0,
                        "credit": 100.0,
                    },
                ),
            ],
        }
        vals.update(overrides)
        return self.Template.create(vals)

    def test_unbalanced_template_rejected(self):
        with self.assertRaises(ValidationError):
            self.Template.create(
                {
                    "name": "bad",
                    "company_id": self.company.id,
                    "journal_id": self.journal.id,
                    "next_date": date.today(),
                    "line_ids": [
                        (0, 0, {"account_id": self.account_debit.id, "debit": 50.0}),
                        (0, 0, {"account_id": self.account_credit.id, "credit": 99.0}),
                    ],
                }
            )

    def test_cron_generates_balanced_move_and_advances_date(self):
        tpl = self._make_template()
        original_next = tpl.next_date
        self.Template._cron_generate_due()
        moves = self.Move.search([("custom_recurring_template_id", "=", tpl.id)])
        self.assertEqual(len(moves), 1, "cron should create exactly one move")
        self.assertEqual(moves.state, "draft")  # auto_post False
        self.assertEqual(len(moves.line_ids), 2)
        debit_total = sum(moves.line_ids.mapped("debit"))
        credit_total = sum(moves.line_ids.mapped("credit"))
        self.assertEqual(debit_total, credit_total)
        self.assertEqual(debit_total, 100.0)
        self.assertEqual(
            tpl.next_date,
            original_next + relativedelta(months=1),
            "next_date must advance by one period",
        )

    def test_auto_post_posts_move(self):
        tpl = self._make_template(auto_post=True)
        self.Template._cron_generate_due()
        moves = self.Move.search([("custom_recurring_template_id", "=", tpl.id)])
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.state, "posted")

    def test_end_date_blocks_generation(self):
        tpl = self._make_template(
            next_date=date.today() - timedelta(days=10),
            end_date=date.today() - timedelta(days=20),
        )
        # next_date > end_date — cron must skip
        self.Template._cron_generate_due()
        moves = self.Move.search([("custom_recurring_template_id", "=", tpl.id)])
        self.assertEqual(len(moves), 0)

    def test_run_now_creates_one_entry(self):
        tpl = self._make_template()
        tpl.action_run_now()
        moves = self.Move.search([("custom_recurring_template_id", "=", tpl.id)])
        self.assertEqual(len(moves), 1)


@tagged("post_install", "-at_install")
class TestRecurringPaymentTemplate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create({"name": "Recurring Vendor"})
        cls.bank_journal = cls.env["account.journal"].search(
            [
                ("type", "=", "bank"),
                ("company_id", "=", cls.company.id),
            ],
            limit=1,
        )
        if not cls.bank_journal:
            cls.bank_journal = cls.env["account.journal"].create(
                {
                    "name": "Bank Recurring",
                    "code": "BNKR",
                    "type": "bank",
                    "company_id": cls.company.id,
                }
            )

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.env["custom.recurring.payment.template"].create(
                {
                    "name": "bad",
                    "company_id": self.company.id,
                    "partner_id": self.partner.id,
                    "payment_type": "outbound",
                    "journal_id": self.bank_journal.id,
                    "amount": 0,
                    "period": "monthly",
                    "next_date": date.today(),
                }
            )
