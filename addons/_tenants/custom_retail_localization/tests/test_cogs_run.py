# -*- coding: utf-8 -*-
"""Periodic COGS per Operating Unit (``retail.cogs.run``).

Self-contained fixtures: own CoA, own analytic plan, two stores with their own
pos.config, and products whose cost is set directly (standing in for what a
goods receipt would have written into ``standard_price``).
"""

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestCogsRun(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        # AccountTestInvoicingCommon's user is an accountant, not a POS manager,
        # and the fixtures below create pos.config / pos.session records.
        cls.env.user.group_ids |= cls.env.ref("point_of_sale.group_pos_manager")
        Account = cls.env["account.account"]

        cls.cogs_textile = Account.create(
            {
                "name": "COGS-textile",
                "code": "COGSTX",
                "account_type": "expense_direct_cost",
            }
        )
        cls.inv_textile = Account.create(
            {
                "name": "Inventories-textile",
                "code": "INVTX",
                "account_type": "asset_current",
            }
        )
        cls.cogs_acc = Account.create(
            {
                "name": "COGS-accessories",
                "code": "COGSAC",
                "account_type": "expense_direct_cost",
            }
        )
        cls.inv_acc = Account.create(
            {
                "name": "Inventories-accessories",
                "code": "INVAC",
                "account_type": "asset_current",
            }
        )

        cls.categ_textile = cls.env["product.category"].create(
            {
                "name": "Textile",
                "property_account_expense_categ_id": cls.cogs_textile.id,
                "property_stock_valuation_account_id": cls.inv_textile.id,
            }
        )
        cls.categ_acc = cls.env["product.category"].create(
            {
                "name": "Accessories",
                "property_account_expense_categ_id": cls.cogs_acc.id,
                "property_stock_valuation_account_id": cls.inv_acc.id,
            }
        )

        cls.ou_plan = cls.env["account.analytic.plan"].create({"name": "Operating Unit"})
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "COGS Journal",
                "type": "general",
                "code": "COGSJ",
                "company_id": cls.company.id,
            }
        )

        # Two stores, each a warehouse + OU analytic + pos.config.
        cls.wh1 = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.wh2 = cls.env["stock.warehouse"].create(
            {
                "name": "Store 2",
                "code": "ST2",
                "company_id": cls.company.id,
            }
        )
        cls.ou1, cls.ou2 = [
            cls.env["account.analytic.account"].create(
                {
                    "name": name,
                    "plan_id": cls.ou_plan.id,
                    "company_id": cls.company.id,
                }
            )
            for name in ("Store 1", "Store 2")
        ]
        cls.wh1.l10n_ou_analytic_id = cls.ou1.id
        cls.wh2.l10n_ou_analytic_id = cls.ou2.id

        cls.config1 = cls._make_config(cls.wh1, "POS 1")
        cls.config2 = cls._make_config(cls.wh2, "POS 2")

        # Costs stand in for what a goods receipt writes into standard_price.
        cls.jeans = cls._make_product("Jeans", cls.categ_textile, cost=100.0)
        cls.belt = cls._make_product("Belt", cls.categ_acc, cost=40.0)
        cls.uncosted = cls._make_product("Uncosted Tee", cls.categ_textile, cost=0.0)
        cls.bag = cls._make_product("Paper Bag", cls.categ_acc, cost=7.0)
        cls.bag.write({"type": "consu", "is_storable": False})

    @classmethod
    def _make_config(cls, warehouse, name):
        picking_type = cls.env["stock.picking.type"].search(
            [("warehouse_id", "=", warehouse.id), ("code", "=", "outgoing")], limit=1
        )
        return cls.env["pos.config"].create(
            {
                "name": name,
                "company_id": cls.company.id,
                "picking_type_id": picking_type.id,
            }
        )

    @classmethod
    def _make_product(cls, name, categ, cost):
        product = cls.env["product.product"].create(
            {
                "name": name,
                "type": "consu",
                "is_storable": True,
                "categ_id": categ.id,
                "available_in_pos": True,
                "lst_price": cost * 3,
            }
        )
        product.with_company(cls.company).standard_price = cost
        return product

    def _session(self, config):
        """The config's open session, created on first use.

        Odoo forbids more than one open session per pos.config, so successive
        sales at the same store share one — exactly what the retail import does.
        """
        session = self.env["pos.session"].search([("config_id", "=", config.id), ("state", "!=", "closed")], limit=1)
        if not session:
            session = self.env["pos.session"].create({"config_id": config.id})
            session.update_stock_at_closing = False
        return session

    def _sell(self, config, lines, date="2026-06-15"):
        """One POS order of ``[(product, qty)]`` at ``config``."""
        session = self._session(config)
        order = self.env["pos.order"].create(
            {
                "company_id": self.company.id,
                "session_id": session.id,
                "date_order": "%s 10:00:00" % date,
                "amount_tax": 0.0,
                "amount_total": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
                "lines": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "qty": qty,
                            "price_unit": product.lst_price,
                            "price_subtotal": product.lst_price * qty,
                            "price_subtotal_incl": product.lst_price * qty,
                        },
                    )
                    for product, qty in lines
                ],
            }
        )
        order.write({"state": "done"})
        return order

    def _run(self, date_from="2026-06-01", date_to="2026-06-30"):
        return self.env["retail.cogs.run"].create(
            {
                "company_id": self.company.id,
                "date_from": date_from,
                "date_to": date_to,
                "journal_id": self.journal.id,
            }
        )

    def _line(self, run, warehouse, categ):
        return run.line_ids.filtered(lambda l: l.warehouse_id == warehouse and l.product_categ_id == categ)

    # ------------------------------------------------------------------

    def test_01_cogs_is_qty_times_cost_per_ou_and_category(self):
        self._sell(self.config1, [(self.jeans, 3), (self.belt, 2)])
        self._sell(self.config2, [(self.jeans, 5)])
        run = self._run()
        run.action_compute()

        self.assertEqual(self._line(run, self.wh1, self.categ_textile).amount, 300.0)
        self.assertEqual(self._line(run, self.wh1, self.categ_acc).amount, 80.0)
        self.assertEqual(self._line(run, self.wh2, self.categ_textile).amount, 500.0)
        # Each line carries its store's Operating Unit.
        self.assertEqual(self._line(run, self.wh1, self.categ_textile).analytic_account_id, self.ou1)
        self.assertEqual(self._line(run, self.wh2, self.categ_textile).analytic_account_id, self.ou2)
        self.assertEqual(run.total_cogs, 880.0)

    def test_02_refund_lines_reduce_cogs(self):
        self._sell(self.config1, [(self.jeans, 5)])
        self._sell(self.config1, [(self.jeans, -2)])  # customer return
        run = self._run()
        run.action_compute()
        self.assertEqual(self._line(run, self.wh1, self.categ_textile).amount, 300.0)

    def test_03_uncosted_units_are_reported_not_silently_zero(self):
        self._sell(self.config1, [(self.jeans, 1), (self.uncosted, 4)])
        run = self._run()
        run.action_compute()
        line = self._line(run, self.wh1, self.categ_textile)
        self.assertEqual(line.amount, 100.0)  # only the costed unit
        self.assertEqual(line.quantity, 5.0)  # but both were sold
        self.assertEqual(line.zero_cost_qty, 4.0)
        self.assertEqual(run.zero_cost_qty, 4.0)

    def test_04_non_storable_products_are_excluded(self):
        self._sell(self.config1, [(self.bag, 10)])
        run = self._run()
        run.action_compute()
        # A paper bag never sat in inventory, so it releases no cost at all.
        self.assertFalse(self._line(run, self.wh1, self.categ_acc))

    def test_05_sales_outside_the_period_are_ignored(self):
        self._sell(self.config1, [(self.jeans, 3)], date="2026-05-31")
        self._sell(self.config1, [(self.jeans, 7)], date="2026-06-30")
        run = self._run()
        run.action_compute()
        # date_to must include the whole of its last day, not stop at midnight.
        self.assertEqual(self._line(run, self.wh1, self.categ_textile).amount, 700.0)

    def test_06_generated_move_is_balanced_draft_with_ou_on_both_legs(self):
        self._sell(self.config1, [(self.jeans, 3)])
        run = self._run()
        run.action_generate_move()
        move = run.move_id

        self.assertEqual(run.state, "generated")
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.date, fields.Date.to_date("2026-06-30"))
        self.assertEqual(sum(move.line_ids.mapped("debit")), 300.0)
        self.assertEqual(sum(move.line_ids.mapped("credit")), 300.0)

        debit = move.line_ids.filtered(lambda l: l.debit)
        credit = move.line_ids.filtered(lambda l: l.credit)
        self.assertEqual(debit.account_id, self.cogs_textile)
        self.assertEqual(credit.account_id, self.inv_textile)
        expected = {str(self.ou1.id): 100.0}
        self.assertEqual(debit.analytic_distribution, expected)
        self.assertEqual(credit.analytic_distribution, expected)

    def test_07_generating_twice_is_refused(self):
        self._sell(self.config1, [(self.jeans, 1)])
        run = self._run()
        run.action_generate_move()
        with self.assertRaises(UserError):
            run.action_generate_move()

    def test_08_period_without_costed_sales_refuses_to_post_an_empty_entry(self):
        self._sell(self.config1, [(self.uncosted, 5)])
        run = self._run()
        with self.assertRaises(UserError):
            run.action_generate_move()
