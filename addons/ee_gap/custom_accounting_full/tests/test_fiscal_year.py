# -*- coding: utf-8 -*-
"""Fiscal year close validation."""

from __future__ import annotations

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestFiscalYearClose(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.fy = self.env["custom.fiscal.year"].create(
            {
                "name": "FY-Test",
                "code": "FY",
                "company_id": self.company.id,
                "date_from": date(date.today().year, 1, 1),
                "date_to": date(date.today().year, 12, 31),
            }
        )
        self.fy.action_open()
        # Create a journal + a draft move in the period
        self.journal = self.env["account.journal"].create(
            {
                "name": "FY Test J",
                "code": "FYTJ",
                "type": "general",
                "company_id": self.company.id,
            }
        )
        self.acc_d = self.env["account.account"].create(
            {
                "code": "FYTDR",
                "name": "FY Debit",
                "account_type": "expense",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        self.acc_c = self.env["account.account"].create(
            {
                "code": "FYTCR",
                "name": "FY Credit",
                "account_type": "liability_current",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        self.draft_move = self.env["account.move"].create(
            {
                "journal_id": self.journal.id,
                "date": date(date.today().year, 6, 1),
                "move_type": "entry",
                "line_ids": [
                    (0, 0, {"account_id": self.acc_d.id, "debit": 50, "credit": 0}),
                    (0, 0, {"account_id": self.acc_c.id, "debit": 0, "credit": 50}),
                ],
            }
        )

    def test_overlap_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["custom.fiscal.year"].create(
                {
                    "name": "FY-Overlap",
                    "company_id": self.company.id,
                    "date_from": date(date.today().year, 6, 1),
                    "date_to": date(date.today().year + 1, 5, 31),
                }
            )

    def test_close_blocked_when_draft_moves_exist(self):
        wiz = self.env["custom.fiscal.year.close.wizard"].create(
            {
                "fiscal_year_id": self.fy.id,
            }
        )
        self.assertGreaterEqual(wiz.draft_move_count, 1)
        with self.assertRaises(UserError):
            wiz.action_close()
        self.assertEqual(self.fy.state, "open")

    def test_close_succeeds_when_all_posted(self):
        self.draft_move.action_post()
        wiz = self.env["custom.fiscal.year.close.wizard"].create(
            {
                "fiscal_year_id": self.fy.id,
            }
        )
        wiz.action_close()
        self.assertEqual(self.fy.state, "closed")
        self.assertEqual(
            self.fy.company_id.fiscalyear_lock_date,
            self.fy.date_to,
        )
