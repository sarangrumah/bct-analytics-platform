# -*- coding: utf-8 -*-
"""Duplicate vendor bill reference is hard-blocked at posting."""

from __future__ import annotations

from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestBillRefGuard(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.vendor = self.env["res.partner"].create({"name": "Ref Vendor"})
        self.other_vendor = self.env["res.partner"].create({"name": "Other Vendor"})
        self.product = self.env["product.product"].create(
            {
                "name": "Ref Item",
                "type": "consu",
                "standard_price": 10.0,
                "purchase_ok": True,
            }
        )

    def _make_bill(self, ref, partner=None):
        return self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": (partner or self.vendor).id,
                "invoice_date": date.today(),
                "ref": ref,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                            "name": self.product.name,
                        }
                    )
                ],
            }
        )

    def test_duplicate_ref_blocks_posting(self):
        first = self._make_bill("INV-001")
        first.action_post()
        self.assertEqual(first.state, "posted")

        second = self._make_bill("INV-001")
        with self.assertRaises(UserError):
            second.action_post()
        self.assertEqual(second.state, "draft")

    def test_duplicate_against_draft_also_blocks(self):
        # Two drafts share a ref; posting the second must fail because the
        # first (still draft, not cancelled) already claims the reference.
        self._make_bill("DRAFT-DUP")
        second = self._make_bill("DRAFT-DUP")
        with self.assertRaises(UserError):
            second.action_post()

    def test_unique_ref_posts_fine(self):
        bill = self._make_bill("INV-UNIQUE")
        bill.action_post()
        self.assertEqual(bill.state, "posted")

    def test_empty_ref_never_duplicate(self):
        b1 = self._make_bill(False)
        b1.action_post()
        b2 = self._make_bill(False)
        b2.action_post()  # empty ref must not count as a duplicate
        self.assertEqual(b2.state, "posted")

    def test_same_ref_different_vendor_ok(self):
        b1 = self._make_bill("SHARED-REF")
        b1.action_post()
        b2 = self._make_bill("SHARED-REF", partner=self.other_vendor)
        b2.action_post()  # different vendor → not a duplicate
        self.assertEqual(b2.state, "posted")

    def test_cancelled_duplicate_is_ignored(self):
        b1 = self._make_bill("CANCEL-REF")
        b1.action_post()
        b1.button_draft()
        b1.button_cancel()
        b2 = self._make_bill("CANCEL-REF")
        b2.action_post()  # only a cancelled move holds the ref → allowed
        self.assertEqual(b2.state, "posted")
