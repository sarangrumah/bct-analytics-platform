# -*- coding: utf-8 -*-
"""3-way match policy enforcement on vendor bill posting."""

from __future__ import annotations

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestThreeWayMatch(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.vendor = self.env["res.partner"].create({"name": "Match Vendor"})
        self.product = self.env["product.product"].create(
            {
                "name": "Match Item",
                "type": "consu",
                "standard_price": 10.0,
                "list_price": 12.0,
                "purchase_ok": True,
            }
        )
        # Strict block policy on both qty and price
        self.policy = self.env["custom.match.policy"].create(
            {
                "name": "Strict",
                "company_id": self.company.id,
                "qty_tolerance_percent": 0.0,
                "price_tolerance_percent": 0.0,
                "on_qty_mismatch": "block",
                "on_price_mismatch": "block",
            }
        )

    def _create_po_and_bill(self, po_price, bill_price, bill_qty=1.0, po_qty=1.0):
        po = self.env["purchase.order"].create(
            {
                "partner_id": self.vendor.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_qty": po_qty,
                            "price_unit": po_price,
                            "name": self.product.name,
                        },
                    )
                ],
            }
        )
        po.button_confirm()
        # Force qty_received to po_qty so qty match would otherwise pass
        po.order_line.qty_received = po_qty
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.vendor.id,
                "invoice_date": date.today(),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "quantity": bill_qty,
                            "price_unit": bill_price,
                            "purchase_line_id": po.order_line.id,
                            "name": self.product.name,
                        },
                    )
                ],
            }
        )
        return po, bill

    def test_block_on_price_variance(self):
        _po, bill = self._create_po_and_bill(po_price=10.0, bill_price=20.0)
        with self.assertRaises(UserError):
            bill.action_post()

    def test_pass_when_within_tolerance(self):
        self.policy.price_tolerance_percent = 50.0
        _po, bill = self._create_po_and_bill(po_price=10.0, bill_price=12.0)
        bill.action_post()
        self.assertEqual(bill.state, "posted")
        self.assertTrue(bill.custom_match_result_id)
