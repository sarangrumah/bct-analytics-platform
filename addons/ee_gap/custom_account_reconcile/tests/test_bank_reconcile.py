# -*- coding: utf-8 -*-
from datetime import date

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestBankReconcile(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.partner = cls.env["res.partner"].create({"name": "BankRec Customer"})
        cls.product = cls.env["product.product"].create({"name": "BankRec Service", "type": "service"})

    def _invoice(self, amount, inv_date=None):
        inv = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "invoice_date": inv_date or date(2026, 7, 1),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        inv.action_post()
        return inv

    def _st_line(self, amount, ref="TRF"):
        return self.env["account.bank.statement.line"].create(
            {
                "journal_id": self.journal.id,
                "date": date(2026, 7, 5),
                "payment_ref": ref,
                "partner_id": self.partner.id,
                "amount": amount,
            }
        )

    def test_exact_match_reconciles(self):
        inv = self._invoice(500000.0)
        st = self._st_line(500000.0, ref=inv.name)
        aml = st._get_auto_match_candidate()
        self.assertTrue(aml)
        self.assertEqual(aml.move_id, inv)
        st._reconcile_with_amls(aml)
        self.assertTrue(st.is_reconciled)
        self.assertIn(inv.payment_state, ("paid", "in_payment"))
        # No suspense leg remains.
        self.assertFalse(st._seek_for_lines()[1])

    def test_writeoff_remainder(self):
        inv = self._invoice(500000.0)
        st = self._st_line(490000.0, ref=inv.name)  # 10k bank fee
        fee_account = self.env["account.account"].create(
            {"name": "Test Bank Fees", "code": "TSTBANKFEE", "account_type": "expense"}
        )
        aml = inv.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        st._reconcile_with_amls(aml, writeoff_vals={"account_id": fee_account.id, "name": "Bank fee"})
        self.assertTrue(st.is_reconciled)
        self.assertIn(inv.payment_state, ("paid", "in_payment"))
        fee_line = st.move_id.line_ids.filtered(lambda l: l.account_id == fee_account)
        self.assertAlmostEqual(fee_line.debit, 10000.0, places=2)

    def test_undo_restores_suspense(self):
        inv = self._invoice(250000.0)
        st = self._st_line(250000.0, ref=inv.name)
        st._reconcile_with_amls(st._get_auto_match_candidate())
        self.assertTrue(st.is_reconciled)
        st.action_undo_reconciliation()
        self.assertFalse(st.is_reconciled)
        self.assertTrue(st._seek_for_lines()[1])

    def test_auto_match_ambiguous_skips(self):
        self._invoice(100000.0)
        self._invoice(100000.0)  # two identical open invoices → ambiguous
        st = self._st_line(100000.0)
        self.assertFalse(st._get_auto_match_candidate())
        st.action_auto_match()
        self.assertFalse(st.is_reconciled)

    def test_wizard_preselects_exact(self):
        inv = self._invoice(750000.0)
        st = self._st_line(750000.0, ref=inv.name)
        wiz = self.env["custom.bank.reconcile.wizard"].with_context(default_st_line_id=st.id).create({})
        picked = wiz.candidate_ids.filtered("selected").mapped("aml_id")
        self.assertEqual(picked.move_id, inv)
        self.assertAlmostEqual(wiz.remainder, 0.0, places=2)
        wiz.action_reconcile()
        self.assertTrue(st.is_reconciled)
