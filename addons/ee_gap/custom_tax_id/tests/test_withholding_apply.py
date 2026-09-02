# -*- coding: utf-8 -*-
"""End-to-end: post a vendor bill, verify withholding line + Bupot draft."""

from __future__ import annotations

from odoo.tests import tagged

from .common import TaxIdCommon


@tagged("post_install", "-at_install")
class TestWithholdingApply(TaxIdCommon):
    def test_post_creates_withholding_line(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.action_post()
        self.assertEqual(len(bill.x_custom_withholding_line_ids), 1)
        wh = bill.x_custom_withholding_line_ids[0]
        self.assertEqual(wh.tarif, 2.0)
        self.assertEqual(wh.base_amount, 1_000_000)
        self.assertEqual(wh.tax_amount, 20_000)

    def test_post_bumps_rate_for_no_npwp_vendor(self):
        bill = self._make_vendor_bill(self.vendor_no_npwp, 1_000_000)
        bill.action_post()
        wh = bill.x_custom_withholding_line_ids[0]
        self.assertEqual(wh.tarif, 4.0)
        self.assertEqual(wh.tax_amount, 40_000)

    def test_post_materialises_bupot_draft(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.action_post()
        wh = bill.x_custom_withholding_line_ids[0]
        self.assertTrue(wh.bupot_id)
        self.assertEqual(wh.bupot_id.state, "draft")
        self.assertEqual(wh.bupot_id.jenis_pph, "23")
        self.assertEqual(wh.bupot_id.tarif, 2.0)
        self.assertEqual(wh.bupot_id.pph_terpotong, 20_000)
        self.assertEqual(wh.bupot_id.partner_id, self.vendor_npwp)

    def test_total_withheld_aggregated(self):
        bill = self.Move.create(
            {
                "move_type": "in_invoice",
                "partner_id": self.vendor_npwp.id,
                "journal_id": self.purchase_journal.id,
                "invoice_date": "2026-01-15",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Jasa #1",
                            "product_id": self.product_jasa.id,
                            "quantity": 1.0,
                            "price_unit": 500_000,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": "Jasa #2",
                            "product_id": self.product_jasa.id,
                            "quantity": 1.0,
                            "price_unit": 500_000,
                            "account_id": self.expense_account.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    ),
                ],
            }
        )
        bill.action_post()
        # 2 lines × 2% × 500,000 = 20,000 total
        self.assertEqual(len(bill.x_custom_withholding_line_ids), 2)
        self.assertEqual(bill.x_custom_total_withheld, 20_000)

    def test_idempotent_no_duplicate_on_repost_simulation(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.action_post()
        # Simulate a re-trigger of the helper directly
        bill._custom_apply_withholding()
        self.assertEqual(len(bill.x_custom_withholding_line_ids), 1)

    def _ensure_general_journal(self):
        journal = self.Journal.search([("type", "=", "general"), ("company_id", "=", self.company.id)], limit=1)
        if not journal:
            journal = self.Journal.create(
                {"name": "Misc (test)", "type": "general", "code": "MISC-T", "company_id": self.company.id}
            )
        return journal

    def test_post_books_withholding_to_gl(self):
        self._ensure_general_journal()
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.action_post()
        je = bill.x_custom_withholding_move_id
        self.assertTrue(je, "A withholding GL entry must be posted.")
        self.assertEqual(je.state, "posted")
        # Cr Hutang PPh 23 = 20,000 (owed to the state).
        hutang = je.line_ids.filtered(lambda l: l.account_id == self.hutang_pph_23)
        self.assertAlmostEqual(sum(hutang.mapped("credit")), 20_000, places=2)
        # Dr Utang Usaha (AP) = 20,000 (reduce vendor payable).
        ap = je.line_ids.filtered(lambda l: l.account_id.account_type == "liability_payable")
        self.assertAlmostEqual(sum(ap.mapped("debit")), 20_000, places=2)
        # Entry balances.
        self.assertAlmostEqual(sum(je.line_ids.mapped("debit")), sum(je.line_ids.mapped("credit")), places=2)

    def test_withholding_gl_idempotent(self):
        self._ensure_general_journal()
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.action_post()
        je = bill.x_custom_withholding_move_id
        self.assertTrue(je)
        bill._custom_post_withholding_entry()  # re-trigger
        self.assertEqual(bill.x_custom_withholding_move_id, je, "Must not create a 2nd entry.")

    def test_withholding_gl_can_be_disabled(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_tax_id.withholding_gl_posting", "0")
        try:
            bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
            bill.action_post()
            self.assertFalse(bill.x_custom_withholding_move_id, "No GL entry when disabled.")
            self.assertEqual(len(bill.x_custom_withholding_line_ids), 1, "Line still created.")
        finally:
            self.env["ir.config_parameter"].sudo().set_param("custom_tax_id.withholding_gl_posting", "1")

    def test_line_with_native_pph_tax_is_skipped(self):
        pph_tax = self.env["account.tax"].create(
            {
                "name": "PPh 23% (General 2%) (test)",
                "amount": -2.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": self.company.id,
            }
        )
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.invoice_line_ids.tax_ids = [(6, 0, [pph_tax.id])]
        bill.action_post()
        self.assertFalse(
            bill.x_custom_withholding_line_ids,
            "A line whose native tax already withholds PPh must not be withheld again.",
        )
        self.assertFalse(bill.x_custom_withholding_move_id)

    def test_sales_invoice_does_not_trigger_withholding(self):
        sale = self.Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": self.vendor_npwp.id,
                "invoice_date": "2026-01-15",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Sale",
                            "quantity": 1.0,
                            "price_unit": 1_000_000,
                            "account_id": self.expense_account.id,
                        },
                    )
                ],
            }
        )
        sale._custom_apply_withholding()
        self.assertFalse(sale.x_custom_withholding_line_ids)
