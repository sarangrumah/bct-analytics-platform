# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestDeferred(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Account = cls.env["account.account"]
        cls.company.deferred_expense_account_id = Account.create(
            {
                "name": "Test Prepaid Expenses",
                "code": "TSTPREP",
                "account_type": "asset_prepayments",
            }
        )
        cls.company.deferred_revenue_account_id = Account.create(
            {
                "name": "Test Deferred Revenue",
                "code": "TSTDEFREV",
                "account_type": "liability_current",
            }
        )
        cls.company.deferred_journal_id = cls.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.partner = cls.env["res.partner"].create({"name": "Deferred Vendor"})
        cls.product = cls.env["product.product"].create({"name": "Annual Service", "type": "service"})

    def _make_bill(self, price, start, end):
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner.id,
                "invoice_date": start,
                "date": start,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "quantity": 1,
                            "price_unit": price,
                            "tax_ids": [(5, 0, 0)],
                            "deferred_start_date": start,
                            "deferred_end_date": end,
                        },
                    )
                ],
            }
        )
        return bill

    def test_full_year_spread(self):
        bill = self._make_bill(1200.0, date(2026, 1, 1), date(2026, 12, 31))
        bill.action_post()
        generated = bill.deferred_generated_ids
        deferral = generated.filtered(lambda m: m.deferred_entry_type == "deferral")
        recogs = generated.filtered(lambda m: m.deferred_entry_type == "recognition")
        self.assertEqual(len(deferral), 1)
        self.assertEqual(deferral.state, "posted")
        self.assertEqual(len(recogs), 12)
        # Recognition amounts sum exactly to the deferred amount.
        prepaid = self.company.deferred_expense_account_id
        total_recognized = sum(l.credit - l.debit for l in recogs.mapped("line_ids") if l.account_id == prepaid)
        self.assertAlmostEqual(total_recognized, 1200.0, places=2)
        # Past months posted, future months draft/at_date.
        today = fields.Date.context_today(bill)
        for move in recogs:
            if move.date <= today:
                self.assertEqual(move.state, "posted")
            else:
                self.assertEqual(move.state, "draft")
                self.assertEqual(move.auto_post, "at_date")

    def test_reset_to_draft_cleans_up(self):
        bill = self._make_bill(600.0, date(2026, 1, 1), date(2026, 6, 30))
        bill.action_post()
        self.assertTrue(bill.deferred_generated_ids)
        bill.button_draft()
        self.assertFalse(bill.deferred_generated_ids)

    def test_no_dates_no_entries(self):
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner.id,
                "invoice_date": date(2026, 3, 1),
                "invoice_line_ids": [(0, 0, {"product_id": self.product.id, "quantity": 1, "price_unit": 100.0})],
            }
        )
        bill.action_post()
        self.assertFalse(bill.deferred_generated_ids)
