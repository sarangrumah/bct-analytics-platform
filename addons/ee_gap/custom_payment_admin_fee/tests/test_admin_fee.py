# -*- coding: utf-8 -*-
"""Multi-COA admin fees on the Register Payment wizard."""

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPaymentAdminFee(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.fee_account = cls.env["account.account"].create(
            {
                "name": "Bank Admin Charges",
                "code": "ADMFEE01",
                "account_type": "expense",
            }
        )
        cls.bill = cls.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": cls.partner_a.id,
                "invoice_date": "2026-01-15",
                "date": "2026-01-15",
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Service",
                            "quantity": 1,
                            "price_unit": 1000000.0,
                            "tax_ids": [],
                        }
                    )
                ],
            }
        )
        cls.bill.action_post()

    def _wizard(self, moves):
        return self.env["account.payment.register"].with_context(active_model="account.move", active_ids=moves.ids)

    def test_fee_adds_to_cash_out_and_bill_reconciles(self):
        wizard = self._wizard(self.bill).create({})
        wizard.admin_fee_line_ids = [
            Command.create({"name": "Biaya Admin Bank", "account_id": self.fee_account.id, "amount": 1500.0})
        ]
        wizard._onchange_admin_fee_line_ids()

        self.assertEqual(wizard.amount, 1001500.0)
        self.assertEqual(wizard.admin_fee_total, 1500.0)
        self.assertFalse(wizard.show_payment_difference)

        payment = wizard._create_payments()
        lines = payment.move_id.line_ids

        fee_line = lines.filtered(lambda l: l.account_id == self.fee_account)
        self.assertEqual(len(fee_line), 1)
        self.assertEqual(fee_line.debit, 1500.0)

        liquidity = lines.filtered(lambda l: l.account_id == payment.outstanding_account_id)
        self.assertEqual(liquidity.credit, 1001500.0)

        payable = lines.filtered(lambda l: l.account_id.account_type == "liability_payable")
        self.assertEqual(payable.debit, 1000000.0)

        self.assertEqual(self.bill.payment_state, "paid")
        self.assertEqual(self.bill.amount_residual, 0.0)

    def test_negative_fee_nets_off_customer_receipt(self):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-01-15",
                "date": "2026-01-15",
                "invoice_line_ids": [
                    Command.create({"name": "Sale", "quantity": 1, "price_unit": 500000.0, "tax_ids": []})
                ],
            }
        )
        invoice.action_post()

        wizard = self._wizard(invoice).create({})
        wizard.admin_fee_line_ids = [
            Command.create({"name": "Biaya Transfer", "account_id": self.fee_account.id, "amount": -2500.0})
        ]
        wizard._onchange_admin_fee_line_ids()
        self.assertEqual(wizard.amount, 497500.0)

        payment = wizard._create_payments()
        lines = payment.move_id.line_ids
        self.assertEqual(lines.filtered(lambda l: l.account_id == self.fee_account).debit, 2500.0)
        self.assertEqual(lines.filtered(lambda l: l.account_id == payment.outstanding_account_id).debit, 497500.0)
        self.assertEqual(invoice.payment_state, "paid")

    def test_removing_fee_restores_plain_amount(self):
        wizard = self._wizard(self.bill).create({})
        wizard.admin_fee_line_ids = [
            Command.create({"name": "Fee", "account_id": self.fee_account.id, "amount": 1500.0})
        ]
        wizard._onchange_admin_fee_line_ids()
        wizard.admin_fee_line_ids = [Command.clear()]
        wizard._onchange_admin_fee_line_ids()
        self.assertEqual(wizard.amount, 1000000.0)

    def _second_bill(self, price_unit=250000.0):
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner_a.id,
                "invoice_date": "2026-01-20",
                "date": "2026-01-20",
                "invoice_line_ids": [
                    Command.create({"name": "Freight", "quantity": 1, "price_unit": price_unit, "tax_ids": []})
                ],
            }
        )
        bill.action_post()
        return bill

    def test_multiple_bills_one_fee_single_payment(self):
        """Several bills paid in one transfer carry a single admin fee."""
        bills = self.bill + self._second_bill()
        wizard = self._wizard(bills).create({})
        self.assertTrue(wizard.can_group_payments)
        self.assertFalse(wizard.group_payment)

        wizard.admin_fee_line_ids = [
            Command.create({"name": "Biaya Admin Bank", "account_id": self.fee_account.id, "amount": 1500.0})
        ]
        wizard._onchange_admin_fee_line_ids()

        # Adding the fee opts into a single grouped payment.
        self.assertTrue(wizard.group_payment)
        self.assertEqual(wizard.amount, 1251500.0)

        payments = wizard._create_payments()
        self.assertEqual(len(payments), 1)

        lines = payments.move_id.line_ids
        fee_line = lines.filtered(lambda line: line.account_id == self.fee_account)
        self.assertEqual(len(fee_line), 1, "the fee is charged once, not once per bill")
        self.assertEqual(fee_line.debit, 1500.0)
        self.assertEqual(
            lines.filtered(lambda line: line.account_id == payments.outstanding_account_id).credit, 1251500.0
        )

        self.assertEqual(bills.mapped("payment_state"), ["paid", "paid"])
        self.assertEqual(sum(bills.mapped("amount_residual")), 0.0)

    def test_ungrouped_multi_bill_with_fee_is_rejected(self):
        """Untick Group Payments after adding a fee -> refuse, do not split it."""
        bills = self.bill + self._second_bill()
        wizard = self._wizard(bills).create({})
        wizard.admin_fee_line_ids = [
            Command.create({"name": "Fee", "account_id": self.fee_account.id, "amount": 1500.0})
        ]
        wizard._onchange_admin_fee_line_ids()
        wizard.group_payment = False
        with self.assertRaises(UserError):
            wizard._create_payments()

    def test_hand_edited_amount_is_rejected(self):
        wizard = self._wizard(self.bill).create({})
        wizard.admin_fee_line_ids = [
            Command.create({"name": "Fee", "account_id": self.fee_account.id, "amount": 1500.0})
        ]
        wizard._onchange_admin_fee_line_ids()
        wizard.amount = 900000.0
        with self.assertRaises(UserError):
            wizard._create_payments()
