# -*- coding: utf-8 -*-
"""Feature #9 — Trade/Non-Trade split + Operating-Unit dimension.

Self-contained fixtures (own CoA via AccountTestInvoicingCommon, own analytic
plan / store journal / mapping / valuated product) so the suite does not depend
on the seeded EBR data.
"""

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestTradeOuSplit(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        Account = cls.env["account.account"]

        # --- Payable + GR/IR accounts for each stream -------------------
        cls.trade_payable = cls.company_data["default_account_payable"]
        cls.nontrade_payable = Account.create(
            {
                "name": "Non Trade Payable - Third parties",
                "code": "NTPAY01",
                "account_type": "liability_payable",
                "reconcile": True,
            }
        )
        # GR/IR clearing is a current-liability control account in the real EBR
        # chart (e.g. "GR/IR clearing-Non Trade Payables", account_type
        # liability_current, reconcilable) — NOT a liability_payable subtype, so
        # product lines routed here on a bill don't trip the payable due-date rule.
        cls.grir_nontrade = Account.create(
            {
                "name": "GR/IR Non Trade",
                "code": "NTGRIR01",
                "account_type": "liability_current",
                "reconcile": True,
            }
        )

        # --- Valuation wiring: valuation acct -> trade GR/IR variation ---
        cls.stock_valuation = Account.create(
            {
                "name": "Inventories-textile",
                "code": "STKVAL01",
                "account_type": "asset_current",
            }
        )
        cls.grir_trade = Account.create(
            {
                "name": "GR/IR Trade-textile",
                "code": "TGRIR01",
                "account_type": "liability_current",
                "reconcile": True,
            }
        )
        cls.stock_valuation.account_stock_variation_id = cls.grir_trade.id
        cls.stock_journal = cls.env["account.journal"].create(
            {
                "name": "Inventory Valuation",
                "type": "general",
                "code": "STJX",
                "company_id": cls.company.id,
            }
        )

        # --- Operating-Unit analytic + per-store purchase journal --------
        cls.ou_plan = cls.env["account.analytic.plan"].create({"name": "Operating Unit"})
        cls.ou_analytic = cls.env["account.analytic.account"].create(
            {
                "name": "Store 1",
                "plan_id": cls.ou_plan.id,
                "company_id": cls.company.id,
            }
        )
        cls.store_journal = cls.env["account.journal"].create(
            {
                "name": "Pembelian - Store 1",
                "type": "purchase",
                "code": "PB001",
                "company_id": cls.company.id,
            }
        )
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.warehouse.write(
            {
                "l10n_ou_analytic_id": cls.ou_analytic.id,
                "l10n_purchase_journal_id": cls.store_journal.id,
            }
        )

        # --- Account mapping --------------------------------------------
        Map = cls.env["retail.purchase.account.map"]
        Map.create(
            {
                "company_id": cls.company.id,
                "purchase_type": "trade",
                "payable_account_id": cls.trade_payable.id,
            }
        )
        Map.create(
            {
                "company_id": cls.company.id,
                "purchase_type": "non_trade",
                "payable_account_id": cls.nontrade_payable.id,
                "grir_account_id": cls.grir_nontrade.id,
            }
        )

        # --- Valuated storable product ----------------------------------
        # Non-trade default expense account + a service product with NO expense
        # account of its own, to exercise the fallback.
        cls.nt_expense = Account.create({"name": "Non-Trade Opex", "code": "NTEXP01", "account_type": "expense"})
        cls.env["retail.purchase.account.map"]._get_map(cls.company, "non_trade").expense_account_id = cls.nt_expense.id
        cls.categ_no_exp = cls.env["product.category"].create({"name": "No-Expense"})
        cls.categ_no_exp.property_account_expense_categ_id = False
        cls.service_no_acct = cls.env["product.product"].create(
            {"name": "Opex Service", "type": "service", "purchase_ok": True, "categ_id": cls.categ_no_exp.id}
        )
        cls.service_no_acct.property_account_expense_id = False

        cls.categ = cls.env["product.category"].create(
            {
                "name": "Textile RT",
                "property_cost_method": "standard",
                "property_valuation": "real_time",
                "property_stock_valuation_account_id": cls.stock_valuation.id,
                "property_stock_journal": cls.stock_journal.id,
                # Give the trade category its own expense account so trade bills do
                # not depend on the company fallback (which we clear below to make the
                # non-trade expense-fallback path reachable in test_07).
                "property_account_expense_categ_id": cls.company_data["default_account_expense"].id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "the apparel brand 501",
                "type": "consu",
                "is_storable": True,
                "categ_id": cls.categ.id,
                "standard_price": 100.0,
                "list_price": 150.0,
            }
        )
        cls.vendor = cls.partner_a
        cls.env["ir.config_parameter"].sudo().set_param("custom_retail_localization.suppress_gr_journal", "0")
        # Remove the company-level default expense so a product with no
        # product/category expense account resolves to *no* account — the exact
        # condition the non-trade expense fallback is meant to cover.
        cls.company.expense_account_id = False

    # ------------------------------------------------------------------
    def _make_po(self, ptype, date_order=None, qty=5):
        vals = {
            "partner_id": self.vendor.id,
            "l10n_purchase_type": ptype,
            "order_line": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "name": self.product.name,
                        "product_qty": qty,
                        "price_unit": 100.0,
                    }
                )
            ],
        }
        if date_order:
            vals["date_order"] = date_order
        return self.env["purchase.order"].create(vals)

    def _receive(self, po):
        po.button_confirm()
        picking = po.picking_ids
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        return picking

    def _bill(self, po):
        po.action_create_invoice()
        bill = po.invoice_ids
        bill.invoice_date = fields.Date.context_today(bill)
        bill.action_post()
        return bill

    def _bill_dated(self, po, date_str):
        po.action_create_invoice()
        bill = po.invoice_ids
        # both the invoice date and the accounting date drive the monthly counter
        bill.write({"invoice_date": date_str, "date": date_str})
        bill.action_post()
        return bill

    # ------------------------------------------------------------------
    # 1. Numbering
    # ------------------------------------------------------------------
    def test_01_numbering_prefixes_and_monthly_reset(self):
        t1 = self._make_po("trade", date_order="2026-07-05 10:00:00")
        t2 = self._make_po("trade", date_order="2026-07-20 10:00:00")
        nt1 = self._make_po("non_trade", date_order="2026-07-10 10:00:00")
        aug = self._make_po("trade", date_order="2026-08-02 10:00:00")

        self.assertTrue(t1.name.startswith("PO/T/EBR/2026/07/"), t1.name)
        self.assertTrue(nt1.name.startswith("PO/NT/EBR/2026/07/"), nt1.name)
        # consecutive within the same month for the same stream
        self.assertEqual(int(t1.name.split("/")[-1]) + 1, int(t2.name.split("/")[-1]))
        # 5-digit padding
        self.assertEqual(len(t1.name.split("/")[-1]), 5)
        # monthly reset
        self.assertTrue(aug.name.startswith("PO/T/EBR/2026/08/"), aug.name)
        self.assertEqual(int(aug.name.split("/")[-1]), 1)

    # ------------------------------------------------------------------
    # 2. Payable routing per stream
    # ------------------------------------------------------------------
    def test_02_payable_account_per_stream(self):
        trade_bill = self._bill(self._trade_po_received())
        nt_bill = self._bill(self._nontrade_po_received())

        trade_pay = trade_bill.line_ids.filtered(lambda l: l.display_type == "payment_term")
        nt_pay = nt_bill.line_ids.filtered(lambda l: l.display_type == "payment_term")
        self.assertEqual(trade_pay.account_id, self.trade_payable)
        self.assertEqual(nt_pay.account_id, self.nontrade_payable)

    # ------------------------------------------------------------------
    # 3. Bill posts to the store purchase journal
    # ------------------------------------------------------------------
    def test_03_bill_uses_store_journal(self):
        bill = self._bill(self._trade_po_received())
        self.assertEqual(bill.journal_id, self.store_journal)
        self.assertEqual(bill.l10n_purchase_type, "trade")

    # ------------------------------------------------------------------
    # 4. Operating-Unit analytic on PO + bill + GR lines
    # ------------------------------------------------------------------
    def test_04_operating_unit_analytic(self):
        po = self._trade_po_received()
        ou_key = str(self.ou_analytic.id)
        # PO line
        pol = po.order_line
        self.assertIn(ou_key, self._dist_ids(pol.analytic_distribution))
        # bill product line
        bill = self._bill(po)
        prod_line = bill.line_ids.filtered(lambda l: l.display_type == "product")
        self.assertIn(ou_key, self._dist_ids(prod_line.analytic_distribution))
        # GR journal line
        gr = self.env["account.move"].search([("ref", "like", "GR-VAL:")], limit=1)
        self.assertTrue(gr, "GR valuation journal not posted")
        self.assertTrue(all(ou_key in self._dist_ids(l.analytic_distribution) for l in gr.line_ids))

    # ------------------------------------------------------------------
    # 5. GR/IR routing: trade -> per-category; non-trade -> mapping
    # ------------------------------------------------------------------
    def test_05_grir_routing(self):
        self._trade_po_received()
        gr_trade = self.env["account.move"].search([("ref", "like", "GR-VAL:")], order="id desc", limit=1)
        self.assertIn(self.grir_trade, gr_trade.line_ids.account_id)

        self._nontrade_po_received()
        gr_nt = self.env["account.move"].search([("ref", "like", "GR-VAL:")], order="id desc", limit=1)
        self.assertIn(self.grir_nontrade, gr_nt.line_ids.account_id)
        self.assertNotIn(self.grir_trade, gr_nt.line_ids.account_id)

    # ------------------------------------------------------------------
    # 6. Per-store P&L slice via OU analytic
    # ------------------------------------------------------------------
    def test_06_pnl_by_operating_unit(self):
        bill = self._bill(self._trade_po_received())
        lines = self.env["account.move.line"].search(
            [
                ("company_id", "=", self.company.id),
                ("analytic_distribution", "!=", False),
            ]
        )
        matched = lines.filtered(lambda l: str(self.ou_analytic.id) in self._dist_ids(l.analytic_distribution))
        self.assertIn(bill.line_ids.filtered(lambda l: l.display_type == "product"), matched)

    # ------------------------------------------------------------------
    # 7. Non-trade expense fallback for products without an expense account
    # ------------------------------------------------------------------
    def test_07_non_trade_expense_fallback(self):
        po = self.env["purchase.order"].create(
            {
                "partner_id": self.vendor.id,
                "l10n_purchase_type": "non_trade",
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.service_no_acct.id,
                            "name": self.service_no_acct.name,
                            "product_qty": 1,
                            "price_unit": 500000.0,
                        }
                    )
                ],
            }
        )
        po.button_confirm()
        bill = self._bill(po)
        prod_line = bill.line_ids.filtered(lambda l: l.display_type == "product")
        self.assertEqual(bill.state, "posted")
        self.assertEqual(prod_line.account_id, self.nt_expense)

    # ------------------------------------------------------------------
    # 8. Vendor bill clears the GR/IR accrual (Dr GR/IR clearing / Cr AP)
    #    — storable, real-time product. Bill must debit the SAME GR/IR
    #    account the receipt credited so the accrual nets to zero, instead
    #    of posting COGS/expense against AP (the reported bug).
    # ------------------------------------------------------------------
    def _grir_balance(self, account):
        return sum(
            self.env["account.move.line"]
            .search(
                [
                    ("account_id", "=", account.id),
                    ("parent_state", "=", "posted"),
                ]
            )
            .mapped("balance")
        )

    def test_08_bill_clears_grir_trade(self):
        bill = self._bill(self._trade_po_received())
        prod_line = bill.line_ids.filtered(lambda l: l.display_type == "product")
        # bill debits the same GR/IR account the receipt credited (per-category)
        self.assertEqual(prod_line.account_id, self.grir_trade)
        # GR/IR nets to zero across receipt (Cr) + bill (Dr)
        self.assertAlmostEqual(self._grir_balance(self.grir_trade), 0.0, places=2)

    def test_09_bill_clears_grir_nontrade(self):
        bill = self._bill(self._nontrade_po_received())
        prod_line = bill.line_ids.filtered(lambda l: l.display_type == "product")
        # non-trade routes to the mapping's GR/IR account on both legs
        self.assertEqual(prod_line.account_id, self.grir_nontrade)
        self.assertAlmostEqual(self._grir_balance(self.grir_nontrade), 0.0, places=2)

    # ------------------------------------------------------------------
    # 11. Vendor-bill numbering: BILL/T/EBR (trade) vs BILL/NT/EBR (non-trade),
    #     monthly reset, independent counters. (Finance-AP #8 / Accounting #16)
    # ------------------------------------------------------------------
    def test_11_bill_numbering_prefixes_and_reset(self):
        # Two trade bills in Jul, one non-trade in Jul, one trade in Aug.
        t1 = self._bill_dated(self._trade_po_received(), "2026-07-05")
        t2 = self._bill_dated(self._trade_po_received(), "2026-07-20")
        nt1 = self._bill_dated(self._nontrade_po_received(), "2026-07-10")
        aug = self._bill_dated(self._trade_po_received(), "2026-08-03")

        self.assertTrue(t1.name.startswith("BILL/T/EBR/2026/07/"), t1.name)
        self.assertTrue(nt1.name.startswith("BILL/NT/EBR/2026/07/"), nt1.name)
        # 5-digit padding
        self.assertEqual(len(t1.name.split("/")[-1]), 5)
        # consecutive within the same month + stream, independent of non-trade
        self.assertEqual(int(t1.name.split("/")[-1]) + 1, int(t2.name.split("/")[-1]))
        # monthly reset for the trade stream
        self.assertTrue(aug.name.startswith("BILL/T/EBR/2026/08/"), aug.name)
        self.assertEqual(int(aug.name.split("/")[-1]), 1)

    # ------------------------------------------------------------------
    # 12. Bank payment numbering: <last-4 of rekening>/YYYY/MM/### (Finance-AP #9)
    # ------------------------------------------------------------------
    def test_12_bank_payment_numbering(self):
        partner_bank = self.env["res.partner.bank"].create(
            {"acc_number": "2687778282", "partner_id": self.company.partner_id.id}
        )
        bankj = self.env["account.journal"].create(
            {
                "name": "Bank Out Test",
                "type": "bank",
                "code": "BKOT",
                "company_id": self.company.id,
                "bank_account_id": partner_bank.id,
            }
        )
        bill = self._bill(self._trade_po_received())
        reg = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=bill.ids)
            .create({"journal_id": bankj.id, "payment_date": "2026-07-15"})
        )
        pay = reg._create_payments()
        # last-4 of 2687778282 = 8282, monthly-reset 3-digit counter
        self.assertRegex(pay.move_id.name or "", r"^8282/2026/07/\d{3}$")

    def test_10_periodic_mode_keeps_expense(self):
        # suppress switch ON -> no per-receipt accrual, so the bill product line
        # must NOT be routed to GR/IR (keeps native/category expense account).
        self.env["ir.config_parameter"].sudo().set_param("custom_retail_localization.suppress_gr_journal", "1")
        try:
            bill = self._bill(self._trade_po_received())
            prod_line = bill.line_ids.filtered(lambda l: l.display_type == "product")
            self.assertNotEqual(prod_line.account_id, self.grir_trade)
        finally:
            self.env["ir.config_parameter"].sudo().set_param("custom_retail_localization.suppress_gr_journal", "0")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _dist_ids(distribution):
        ids = set()
        for key in distribution or {}:
            ids.update(key.split(","))
        return ids

    def _trade_po_received(self):
        po = self._make_po("trade")
        self._receive(po)
        return po

    def _nontrade_po_received(self):
        po = self._make_po("non_trade")
        self._receive(po)
        return po
