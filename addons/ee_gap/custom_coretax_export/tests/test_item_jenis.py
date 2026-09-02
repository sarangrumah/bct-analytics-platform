# -*- coding: utf-8 -*-
""" "Jenis Barang Jasa" resolution for e-Faktur Keluaran OF item rows.

The interesting case is the down-payment line, which carries no product of its
own and used to be reported as "Barang" regardless of what was being sold.
"""

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_coretax_export")
class TestCoretaxItemJenis(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wizard = cls.env["custom.coretax.template.export.wizard"]
        cls.service = cls.env["product.product"].create(
            {"name": "Jasa Test", "type": "service", "invoice_policy": "order"}
        )
        cls.goods = cls.env["product.product"].create({"name": "Barang Test", "type": "consu"})

    def _invoice_line(self, product):
        # sudo() throughout: custom_tax_id's withholding lines are out of reach
        # for the common's test user, and none of that is what is under test.
        invoice = (
            self.env["account.move"]
            .sudo()
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": self.partner_a.id,
                    "invoice_date": "2026-07-01",
                    "invoice_line_ids": [(0, 0, {"product_id": product.id, "quantity": 1, "price_unit": 100.0})],
                }
            )
        )
        return invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")[0]

    def _dp_line(self, products):
        order = (
            self.env["sale.order"]
            .sudo()
            .create(
                {
                    "partner_id": self.partner_a.id,
                    "order_line": [(0, 0, {"product_id": p.id, "product_uom_qty": 1}) for p in products],
                }
            )
        )
        order.action_confirm()
        wizard = (
            self.env["sale.advance.payment.inv"]
            .sudo()
            .with_context(active_ids=order.ids, active_model="sale.order")
            .create({"advance_payment_method": "percentage", "amount": 50.0})
        )
        invoice = wizard._create_invoices(order)
        return invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product")[0]

    # --- unchanged behaviour for ordinary lines ---------------------------
    def test_service_product_is_jasa(self):
        self.assertEqual(self.wizard._item_jenis(self._invoice_line(self.service)), "Jasa")

    def test_goods_product_is_barang(self):
        self.assertEqual(self.wizard._item_jenis(self._invoice_line(self.goods)), "Barang")

    def test_line_without_product_is_barang(self):
        line = self._invoice_line(self.goods)
        line.product_id = False
        self.assertEqual(self.wizard._item_jenis(line), "Barang")

    # --- the fix: down-payment lines -------------------------------------
    def test_down_payment_for_services_is_jasa(self):
        self.assertEqual(self.wizard._item_jenis(self._dp_line(self.service)), "Jasa")

    def test_down_payment_for_goods_is_barang(self):
        self.assertEqual(self.wizard._item_jenis(self._dp_line(self.goods)), "Barang")

    # A mixed order has no truthful single answer; "Barang" is the safe one.
    def test_down_payment_for_mixed_order_is_barang(self):
        self.assertEqual(self.wizard._item_jenis(self._dp_line(self.service | self.goods)), "Barang")
