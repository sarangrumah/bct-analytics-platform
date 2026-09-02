# -*- coding: utf-8 -*-
from datetime import datetime

from odoo.tests import TransactionCase, tagged

from odoo.addons.custom_doc_numbering.hooks import _upsert_sequence


@tagged("post_install", "-at_install")
class TestTenantNumbering(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Test Numbering Co", "x_doc_code": "TST"})
        cls.env.user.company_ids = [(4, cls.company.id)]
        # Quotation (sale.order) + Sales Order sequences for this company.
        _upsert_sequence(
            cls.env,
            "sale.order",
            "Test SQ",
            "SQ/TST/%(range_year)s/%(range_month)s/",
            cls.company,
        )
        _upsert_sequence(
            cls.env,
            "tenant.sale_order",
            "Test SO",
            "SO/TST/%(range_year)s/%(range_month)s/",
            cls.company,
        )

    def _next(self, code, dt):
        return self.env["ir.sequence"].with_company(self.company).next_by_code(code, sequence_date=dt)

    def test_monthly_reset(self):
        """Counter restarts at 001 in a new month and matches the date range."""
        jun1 = self._next("sale.order", datetime(2026, 6, 1))
        jun2 = self._next("sale.order", datetime(2026, 6, 20))
        jul1 = self._next("sale.order", datetime(2026, 7, 5))
        self.assertEqual(jun1, "SQ/TST/2026/06/001")
        self.assertEqual(jun2, "SQ/TST/2026/06/002")
        self.assertEqual(jul1, "SQ/TST/2026/07/001")

    def test_quotation_then_sales_order_renumber(self):
        """Quotation reads SQ/...; confirming re-numbers to SO/... and keeps SQ."""
        partner = self.env["res.partner"].create({"name": "Cust"})
        product = self.env["product.product"].create({"name": "Svc", "type": "service", "list_price": 100.0})
        order = (
            self.env["sale.order"]
            .with_company(self.company)
            .create(
                {
                    "partner_id": partner.id,
                    "company_id": self.company.id,
                    "date_order": datetime(2026, 6, 10),
                    "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1})],
                }
            )
        )
        self.assertTrue(order.name.startswith("SQ/TST/2026/06/"))
        sq_name = order.name
        order.action_confirm()
        self.assertTrue(order.name.startswith("SO/TST/2026/06/"))
        self.assertEqual(order.x_quotation_name, sq_name)
