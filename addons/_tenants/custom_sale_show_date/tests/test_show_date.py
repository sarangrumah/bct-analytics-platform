# -*- coding: utf-8 -*-
"""the tenant show-date: propagation, required gating, and due-date anchoring.

Uses AccountTestInvoicingCommon so the test company has a real chart of
accounts + journals (needed to create invoices from sales orders). A single
company is used and the gate flag is toggled per case — TransactionCase rolls
back each method to the setUpClass state, so toggles don't leak.
"""

from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_sale_show_date")
class TestArkaShowDate(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.x_custom_show_date_enabled = True  # treat as "PT the tenant"

        cls.partner = cls.partner_a
        cls.product = cls.product_a
        cls.product.invoice_policy = "order"  # invoiceable on confirm

        cls.term30 = cls.env["account.payment.term"].create(
            {
                "name": "30 Days After (test)",
                "line_ids": [(0, 0, {"value": "percent", "value_amount": 100.0, "nb_days": 30})],
            }
        )
        cls.show = date(2026, 9, 1)

    def _make_so(self, show_date=False):
        # sudo(): AccountTestInvoicingCommon's user has accounting but not Sales
        # rights; the feature logic under test is independent of access groups.
        return (
            self.env["sale.order"]
            .with_company(self.company)
            .sudo()
            .create(
                {
                    "partner_id": self.partner.id,
                    "company_id": self.company.id,
                    "payment_term_id": self.term30.id,
                    "x_custom_show_date": show_date,
                    "order_line": [(0, 0, {"product_id": self.product.id, "product_uom_qty": 1})],
                }
            )
        )

    # (1) field propagates SO -> invoice via _prepare_invoice
    def test_show_date_propagates_to_invoice(self):
        so = self._make_so(self.show)
        so.action_confirm()
        invoice = so._create_invoices()
        self.assertEqual(invoice.x_custom_show_date, self.show)

    # (2) required enforced only when company flag is on
    def test_required_only_when_flag_on(self):
        so = self._make_so(show_date=False)
        with self.assertRaises(UserError):
            so.action_confirm()

        self.company.x_custom_show_date_enabled = False
        so2 = self._make_so(show_date=False)
        so2.action_confirm()  # must NOT raise
        self.assertEqual(so2.state, "sale")

    # (3) due date anchored to show_date for the flagged company
    def test_due_date_anchored_to_show_date(self):
        so = self._make_so(self.show)
        so.action_confirm()
        invoice = so._create_invoices()
        invoice.invoice_date = date(2026, 6, 1)  # deliberately != show date
        # 30 days after SHOW date (2026-09-01) => 2026-10-01, NOT 2026-07-01.
        self.assertEqual(invoice.invoice_date_due, date(2026, 10, 1))
        receivable = invoice.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertEqual(receivable.date_maturity, date(2026, 10, 1))

    # (4) event block appended to the line description and carried to the invoice
    def test_event_description_block(self):
        so = self._make_so(self.show)
        so.write(
            {
                "x_custom_event_name": "Danone",
                "x_custom_event_location": "Taman Bhagawan Bali",
                "x_custom_dp_note": "DP 50%",
            }
        )
        line = so.order_line[0]
        self.assertEqual(
            line.name.splitlines()[-1],
            "Event Danone, Lokasi Taman Bhagawan Bali, 01.09.26, DP 50%",
        )
        self.assertTrue(line.name.startswith(self.product.name))

        so.action_confirm()
        invoice = so._create_invoices()
        invoice_line = invoice.invoice_line_ids.filtered(lambda x: x.product_id == self.product)
        self.assertEqual(invoice_line.name, line.name)

    # (5) empty event data leaves the core description untouched
    def test_event_description_absent_when_unset(self):
        so = self._make_so(self.show)
        so.write({"x_custom_show_date": False})
        self.assertNotIn("Event", so.order_line[0].name)

    def _make_dp_invoice(self, so, method="percentage", amount=50.0):
        wizard = (
            self.env["sale.advance.payment.inv"]
            .with_company(self.company)
            .sudo()
            .with_context(active_ids=so.ids, active_model="sale.order")
            .create(
                {
                    "advance_payment_method": method,
                    **({"amount": amount} if method == "percentage" else {"fixed_amount": amount}),
                }
            )
        )
        return wizard._create_invoices(so)

    # (7) DP invoice line is labelled with the products, not "Down payment of X%"
    def test_down_payment_line_uses_product_description(self):
        so = self._make_so(self.show)
        so.action_confirm()
        dp_line = self._make_dp_invoice(so).invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(dp_line.name, "%s, 01.09.26 (Uang Muka 50%%)" % self.product.name)
        # Single line: this same string is one cell in the coretax import file.
        self.assertNotIn("\n", dp_line.name)
        self.assertNotIn("Down payment", dp_line.name)

    # (8) every product line of the order is listed, deduplicated, in order.
    # Core splits the down payment into one invoice line per tax grouping, so an
    # order whose products carry different taxes yields several DP lines; each
    # one describes the whole order, which is what the client's paperwork shows.
    def test_down_payment_line_lists_all_products(self):
        so = self._make_so(self.show)
        so.sudo().write({"order_line": [(0, 0, {"product_id": self.product_b.id, "product_uom_qty": 1})]})
        so.action_confirm()
        dp_lines = self._make_dp_invoice(so).invoice_line_ids.filtered(lambda line: line.display_type == "product")
        expected = "%s, %s, 01.09.26 (Uang Muka 50%%)" % (self.product.name, self.product_b.name)
        self.assertTrue(dp_lines)
        self.assertEqual(set(dp_lines.mapped("name")), {expected})

    # (9) fixed-amount DP gets a marker without a percentage
    def test_down_payment_fixed_amount_marker(self):
        so = self._make_so(self.show)
        so.action_confirm()
        dp_line = self._make_dp_invoice(so, method="fixed", amount=100.0).invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(dp_line.name, "%s, 01.09.26 (Uang Muka)" % self.product.name)

    # (9b) the event detail rides the DP line, so it reaches the invoice PDF and
    # the Faktur Pajak "Nama Barang Jasa" cell (which reads this very string).
    def test_down_payment_line_carries_event_detail(self):
        so = self._make_so(self.show)
        so.write(
            {
                "x_custom_event_name": "Danone",
                "x_custom_event_location": "Taman Bhagawan Bali",
                "x_custom_dp_note": "DP 50%",
            }
        )
        so.action_confirm()
        dp_line = self._make_dp_invoice(so).invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertEqual(
            dp_line.name,
            "%s, Event Danone, Lokasi Taman Bhagawan Bali, 01.09.26 (Uang Muka 50%%)" % self.product.name,
        )
        # The trailing marker already says it — the free-text DP note is dropped
        # here so the cell does not read "DP 50% (Uang Muka 50%)".
        self.assertNotIn("DP 50% ", dp_line.name)
        self.assertNotIn("\n", dp_line.name)

    # (10) flag off -> core "Down payment of X%" wording is left intact
    def test_down_payment_line_untouched_when_flag_off(self):
        self.company.x_custom_show_date_enabled = False
        so = self._make_so(self.show)
        so.action_confirm()
        dp_line = self._make_dp_invoice(so).invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertIn("Down payment", dp_line.name)

    # (6) flag off -> standard behaviour (anchored to invoice_date)
    def test_non_flagged_anchors_to_invoice_date(self):
        self.company.x_custom_show_date_enabled = False
        so = self._make_so(self.show)  # show date set, but flag off
        so.action_confirm()
        invoice = so._create_invoices()
        invoice.invoice_date = date(2026, 6, 1)
        # 30 days after INVOICE date (2026-06-01) => 2026-07-01.
        self.assertEqual(invoice.invoice_date_due, date(2026, 7, 1))
