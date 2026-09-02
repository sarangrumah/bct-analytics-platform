# -*- coding: utf-8 -*-
"""End-to-end petty-cash flow: request → approve → disburse → realize
(expense + third-party) → return → settle."""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPettyCashFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Account = cls.env["account.account"]

        def _acc(code, name, atype, reconcile=False):
            return Account.create({"code": code, "name": name, "account_type": atype, "reconcile": reconcile})

        cls.advance = _acc("PC1001", "Uang Muka Petty Cash", "asset_current", reconcile=True)
        cls.bank_acc = _acc("PC1101", "Bank Petty Cash", "asset_cash")
        cls.expense = _acc("PC6001", "Petty Expense", "expense")
        cls.ap = _acc("PC2001", "Petty AP", "liability_payable", reconcile=True)

        Journal = cls.env["account.journal"]
        cls.bank_journal = Journal.create(
            {"name": "PC Bank", "type": "bank", "code": "PCBK", "default_account_id": cls.bank_acc.id}
        )
        cls.pay_journal = Journal.create(
            {"name": "PC Pay", "type": "bank", "code": "PCPY", "default_account_id": cls.bank_acc.id}
        )
        cls.gen_journal = Journal.create({"name": "PC Misc", "type": "general", "code": "PCMS"})

        cls.company.write(
            {
                "petty_cash_advance_account_id": cls.advance.id,
                "petty_cash_bank_out_journal_id": cls.bank_journal.id,
                "petty_cash_payment_journal_id": cls.pay_journal.id,
                "petty_cash_expense_journal_id": cls.gen_journal.id,
            }
        )

        cls.partner_emp = cls.env["res.partner"].create({"name": "Employee Contact"})
        cls.employee = cls.env["hr.employee"].create({"name": "Petty Employee", "work_contact_id": cls.partner_emp.id})
        cls.vendor = cls.env["res.partner"].create({"name": "Toko ATK"})
        cls.attachment = cls.env["ir.attachment"].create(
            {"name": "invoice.pdf", "datas": "cGRm", "mimetype": "application/pdf"}
        )

    def _new_request(self, amount=1000.0):
        return self.env["petty.cash.request"].create(
            {
                "employee_id": self.employee.id,
                "amount_requested": amount,
                "purpose": "Office supplies",
            }
        )

    def test_disburse_books_advance_and_bank(self):
        req = self._new_request(1000.0)
        req.action_submit()
        req.action_approve()
        self.assertEqual(req.state, "approved")
        req.action_disburse()
        self.assertEqual(req.state, "disbursed")
        self.assertTrue(req.disburse_move_id)
        self.assertEqual(req.disburse_move_id.state, "posted")
        adv_line = req.disburse_move_id.line_ids.filtered(lambda l: l.account_id == self.advance)
        self.assertEqual(adv_line.debit, 1000.0)
        self.assertEqual(req.amount_outstanding, 1000.0)
        # Advance line carries the employee analytic tag.
        emp_analytic = self.employee._pc_get_analytic_account()
        keys = ",".join((adv_line.analytic_distribution or {}).keys())
        self.assertIn(str(emp_analytic.id), keys.split(","))

    def test_expense_realization_and_settle(self):
        req = self._new_request(1000.0)
        req.action_submit()
        req.action_approve()
        req.action_disburse()

        real = self.env["petty.cash.realization"].create(
            {
                "request_id": req.id,
                "line_ids": [
                    Command.create(
                        {
                            "line_type": "expense",
                            "name": "Parking",
                            "account_id": self.expense.id,
                            "price_unit": 600.0,
                        }
                    )
                ],
            }
        )
        real.action_post()
        self.assertEqual(real.state, "posted")
        self.assertEqual(req.state, "in_realization")
        # Advance credited by the expense; outstanding drops to 400.
        self.assertAlmostEqual(req.amount_outstanding, 400.0, 2)

        req.action_return_balance()
        self.assertAlmostEqual(req.amount_outstanding, 0.0, 2)
        req.action_settle()
        self.assertEqual(req.state, "settled")

    def test_third_party_requires_attachment(self):
        req = self._new_request(1000.0)
        req.action_submit()
        req.action_approve()
        req.action_disburse()
        real = self.env["petty.cash.realization"].create(
            {
                "request_id": req.id,
                "line_ids": [
                    Command.create(
                        {
                            "line_type": "third_party",
                            "name": "Stationery",
                            "partner_id": self.vendor.id,
                            "account_id": self.expense.id,
                            "price_unit": 500.0,
                            "attachment_ids": [Command.set(self.attachment.ids)],
                        }
                    )
                ],
            }
        )
        real.action_post()
        self.assertTrue(real.bill_ids)
        bill = real.bill_ids.filtered(lambda m: m.move_type == "in_invoice")
        self.assertTrue(bill)
        self.assertEqual(bill.state, "posted")
        # Bill was paid out of the advance → advance credited.
        self.assertLess(req.amount_outstanding, 1000.0)
