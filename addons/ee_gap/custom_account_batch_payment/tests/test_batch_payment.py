# -*- coding: utf-8 -*-
import base64

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestBatchPayment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.partner = cls.env["res.partner"].create({"name": "Batch Vendor"})
        cls.partner_bank = cls.env["res.partner.bank"].create(
            {"acc_number": "1234567890", "partner_id": cls.partner.id}
        )
        cls.payments = cls.env["account.payment"].create(
            [
                {
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": cls.partner.id,
                    "partner_bank_id": cls.partner_bank.id,
                    "amount": amount,
                    "journal_id": cls.journal.id,
                }
                for amount in (100.0, 250.0, 650.0)
            ]
        )
        cls.payments.action_post()

    def _make_batch(self):
        return self.env["custom.account.batch.payment"].create(
            {
                "journal_id": self.journal.id,
                "batch_type": "outbound",
                "payment_ids": [(6, 0, self.payments.ids)],
            }
        )

    def test_validate_and_export(self):
        batch = self._make_batch()
        batch.action_validate()
        self.assertEqual(batch.state, "validated")
        self.assertNotEqual(batch.name, "New")
        self.assertEqual(batch.amount_total, 1000.0)

        batch.export_format_id = self.env.ref("custom_account_batch_payment.format_mandiri_mcm")
        batch.action_generate_export_file()
        self.assertEqual(batch.state, "sent")
        content = base64.b64decode(batch.export_file).decode("utf-8")
        # header + 3 rows
        self.assertEqual(len([l for l in content.splitlines() if l]), 4)
        self.assertIn("1234567890", content)
        self.assertTrue(all(self.payments.mapped("is_sent")))

    def test_empty_batch_blocks_validate(self):
        batch = self.env["custom.account.batch.payment"].create(
            {"journal_id": self.journal.id, "batch_type": "outbound"}
        )
        with self.assertRaises(UserError):
            batch.action_validate()

    def test_missing_bank_account_blocks_export(self):
        self.payments[0].partner_bank_id = False
        batch = self._make_batch()
        batch.action_validate()
        batch.export_format_id = self.env.ref("custom_account_batch_payment.format_bri")
        with self.assertRaises(UserError):
            batch.action_generate_export_file()

    def test_double_batching_blocked(self):
        self._make_batch()
        with self.assertRaises(UserError):
            self.env["account.payment"].with_context(active_ids=self.payments.ids).action_create_batch_from_selection()
