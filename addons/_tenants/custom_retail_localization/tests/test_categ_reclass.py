# -*- coding: utf-8 -*-
"""Product category reclassification (``retail.categ.reclass``).

Fixtures mirror the apparel brand shape: COA-bucket root categories that carry the
Gross Sales / Sales Discount / Sales Return mapping, a store with its own
Operating Unit analytic, and POS orders standing in for what the retail import
posts.
"""

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestCategReclass(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.group_ids |= cls.env.ref("point_of_sale.group_pos_manager")
        # X24DN discounts are booked as a contra-revenue reclass; the reclassification
        # has to move them along with the revenue, so switch the source on explicitly
        # instead of inheriting whatever the host database happens to be set to.
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("retail_import.x24_discount_reclass", "1")
        icp.set_param("retail_import.x31_post_enabled", "0")
        Account = cls.env["account.account"]

        def income(name, code):
            return Account.create({"name": name, "code": code, "account_type": "income"})

        cls.gs_labor = income("Gross Sales-Labor (Service)", "GSLAB")
        cls.gs_misc = income("Gross Sales-miscellaneous", "GSMIS")
        cls.gs_text = income("Gross Sales-textile", "GSTEX")
        cls.sd_labor = income("Sales discount-Labor (Service)", "SDLAB")
        cls.sd_misc = income("Sales Discount-Miscellaneous", "SDMIS")
        cls.sd_text = income("Sales Discount-Textile", "SDTEX")
        cls.sr_labor = income("Sales return-Labor (Service)", "SRLAB")
        cls.sr_misc = income("Sales Return-miscellaneous", "SRMIS")
        cls.clearing = Account.create(
            {"name": "Category Reclass Clearing", "code": "CLEAR", "account_type": "liability_current"}
        )

        Categ = cls.env["product.category"]
        cls.categ_labor = Categ.create(
            {
                "name": "Labor (Service)",
                "property_account_income_categ_id": cls.gs_labor.id,
                "property_account_sales_discount_categ_id": cls.sd_labor.id,
                "property_account_sales_return_categ_id": cls.sr_labor.id,
            }
        )
        cls.categ_misc = Categ.create(
            {
                "name": "Miscellaneous",
                "property_account_income_categ_id": cls.gs_misc.id,
                "property_account_sales_discount_categ_id": cls.sd_misc.id,
                "property_account_sales_return_categ_id": cls.sr_misc.id,
            }
        )
        cls.categ_text = Categ.create(
            {
                "name": "Textile",
                "property_account_income_categ_id": cls.gs_text.id,
                "property_account_sales_discount_categ_id": cls.sd_text.id,
            }
        )
        # A leaf that inherits the bucket's mapping from its parent, the way the
        # X101 merchandising tree hangs under the COA roots.
        cls.categ_misc_leaf = Categ.create({"name": "Patches", "parent_id": cls.categ_misc.id})
        # A company-dependent property can inherit a company-wide ir.default; the
        # the apparel brand databases store an explicit empty one so the parent chain decides.
        cls.categ_misc_leaf.with_company(cls.company).property_account_income_categ_id = False

        cls.journal = cls.env["account.journal"].create(
            {"name": "General Journal", "type": "general", "code": "GLJVT", "company_id": cls.company.id}
        )
        cls.ou_plan = cls.env["account.analytic.plan"].create({"name": "Operating Unit"})
        cls.ou = cls.env["account.analytic.account"].create(
            {"name": "Store 1", "plan_id": cls.ou_plan.id, "company_id": cls.company.id}
        )
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.warehouse.l10n_ou_analytic_id = cls.ou.id
        picking_type = cls.env["stock.picking.type"].search(
            [("warehouse_id", "=", cls.warehouse.id), ("code", "=", "outgoing")], limit=1
        )
        cls.config = cls.env["pos.config"].create(
            {"name": "POS 1", "company_id": cls.company.id, "picking_type_id": picking_type.id}
        )

        # When the optional governance add-on is installed it gates every apply
        # behind two Finance tiers, and its second tier ships with an empty
        # group. These tests are about what gets booked, not who signs, so the
        # test user is put on both benches.
        finance = cls.env.ref(
            "custom_retail_categ_approval.group_categ_reclass_finance_manager", raise_if_not_found=False
        )
        if finance:
            cls.env.user.group_ids |= finance | cls.env.ref("account.group_account_manager")

        cls.patch = cls.env["product.product"].create(
            {
                "name": "Patches S",
                "default_code": "TS1000413",
                "type": "consu",
                "is_storable": False,
                "categ_id": cls.categ_labor.id,
                "available_in_pos": True,
                "lst_price": 100.0,
            }
        )

    # ------------------------------------------------------------------
    def _session(self):
        session = self.env["pos.session"].search(
            [("config_id", "=", self.config.id), ("state", "!=", "closed")], limit=1
        )
        if not session:
            session = self.env["pos.session"].create({"config_id": self.config.id})
            session.update_stock_at_closing = False
        return session

    def _sell(self, product, qty, date="2026-07-15", discount=0.0, is_return=False):
        order = self.env["pos.order"].create(
            {
                "company_id": self.company.id,
                "session_id": self._session().id,
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
                            "ri_src_discount": discount,
                            "ri_is_return": is_return,
                        },
                    )
                ],
            }
        )
        order.write({"state": "done"})
        return order

    def _reclass(self, products=None, categ=None, **kwargs):
        vals = {
            "company_id": self.company.id,
            "product_tmpl_ids": [(6, 0, (products or self.patch).product_tmpl_id.ids)],
            "new_categ_id": (categ or self.categ_misc).id,
            "journal_id": self.journal.id,
            "fallback_date": "2026-08-03",
        }
        vals.update(kwargs)
        return self.env["retail.categ.reclass"].create(vals)

    def _line(self, rec, kind):
        return rec.line_ids.filtered(lambda line: line.kind == kind)

    def _apply(self, rec):
        """Apply a reclassification, walking any approval tiers in the way.

        ``custom_retail_categ_approval`` is optional but, when installed in the
        same database, it gates ``action_apply`` behind a Finance approval.
        These tests are about what gets booked, not about who signs, so they
        sign for whoever is being asked.
        """
        rec.action_apply()
        guard = 0
        while rec.state == "to_approve" and guard < 5:
            guard += 1
            request = rec.x_custom_approval_request_id
            approver = request.pending_approver_ids[:1]
            if not request or request.state != "pending" or not approver:
                break
            request.with_user(approver).action_approve()
        return rec

    # ------------------------------------------------------------------

    def test_01_open_period_keeps_the_original_date(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()

        line = self._line(rec, "income")
        self.assertEqual(len(line), 1)
        self.assertEqual(line.amount, -300.0)  # revenue is a credit balance
        self.assertEqual(line.source_account_id, self.gs_labor)
        self.assertEqual(line.target_account_id, self.gs_misc)
        self.assertEqual(line.origin_date, fields.Date.to_date("2026-07-15"))
        self.assertEqual(line.posting_date, fields.Date.to_date("2026-07-15"))
        self.assertFalse(line.is_period_closed)
        self.assertEqual(line.analytic_account_id, self.ou)
        self.assertEqual(rec.total_amount, 300.0)

    def test_02_closed_period_is_corrected_in_the_current_month(self):
        self._sell(self.patch, 3, date="2026-06-15")
        self.company.sudo().fiscalyear_lock_date = "2026-06-30"
        rec = self._reclass()
        rec.action_compute()

        line = self._line(rec, "income")
        self.assertTrue(line.is_period_closed)
        self.assertEqual(line.origin_date, fields.Date.to_date("2026-06-15"))
        self.assertEqual(line.posting_date, fields.Date.to_date("2026-08-03"))
        self.assertEqual(rec.closed_period_count, 1)
        # The lock itself is never touched.
        self.assertEqual(self.company.fiscalyear_lock_date, fields.Date.to_date("2026-06-30"))

    def test_03_discount_moves_with_the_revenue_it_reduced(self):
        # POS credits Gross Sales net of discount, then the retail import grosses
        # it back up: Dr Sales Discount / Cr Gross Sales.
        self._sell(self.patch, 3, discount=50.0)
        rec = self._reclass()
        rec.action_compute()

        self.assertEqual(self._line(rec, "income").amount, -350.0)
        discount = self._line(rec, "discount")
        self.assertEqual(discount.amount, 50.0)
        self.assertEqual(discount.source_account_id, self.sd_labor)
        self.assertEqual(discount.target_account_id, self.sd_misc)

    def test_04_returns_move_to_the_new_return_account(self):
        self._sell(self.patch, -2, is_return=True)
        rec = self._reclass()
        rec.action_compute()

        line = self._line(rec, "return")
        self.assertEqual(line.amount, 200.0)  # a return is a debit balance
        self.assertEqual(line.source_account_id, self.sr_labor)
        self.assertEqual(line.target_account_id, self.sr_misc)

    def test_05_apply_changes_the_category_and_books_a_balanced_draft_entry(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        self._apply(rec)

        self.assertEqual(rec.state, "applied")
        self.assertEqual(self.patch.categ_id, self.categ_misc)
        move = rec.move_ids
        self.assertEqual(len(move), 1)
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.date, fields.Date.to_date("2026-07-15"))
        self.assertEqual(sum(move.line_ids.mapped("debit")), 300.0)
        self.assertEqual(sum(move.line_ids.mapped("credit")), 300.0)

        debit = move.line_ids.filtered(lambda line: line.debit)
        credit = move.line_ids.filtered(lambda line: line.credit)
        self.assertEqual(debit.account_id, self.gs_labor)
        self.assertEqual(credit.account_id, self.gs_misc)
        expected = {str(self.ou.id): 100.0}
        self.assertEqual(debit.analytic_distribution, expected)
        self.assertEqual(credit.analytic_distribution, expected)

    def test_06_one_entry_per_posting_date(self):
        self._sell(self.patch, 1, date="2026-07-15")
        self._sell(self.patch, 2, date="2026-07-16")
        rec = self._reclass()
        self._apply(rec)
        self.assertEqual(
            sorted(rec.move_ids.mapped("date")),
            [fields.Date.to_date("2026-07-15"), fields.Date.to_date("2026-07-16")],
        )

    def test_07_a_second_reclass_never_books_the_same_turnover_twice(self):
        self._sell(self.patch, 3)
        first = self._reclass()
        self._apply(first)

        second = self._reclass(categ=self.categ_text)
        second.action_compute()
        self.assertFalse(second.line_ids)
        self.assertIn("already been corrected", second.warning_text)

    def test_08_inherited_mapping_resolves_through_the_parent_chain(self):
        self._sell(self.patch, 3)
        rec = self._reclass(categ=self.categ_misc_leaf)
        rec.action_compute()
        # The leaf carries no mapping of its own; Miscellaneous supplies it.
        self.assertEqual(self._line(rec, "income").target_account_id, self.gs_misc)

    def test_09_lines_the_ledger_cannot_back_are_flagged(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        # Nothing was ever posted in this fixture, so the sanity net must fire
        # rather than quietly inventing a balance on Gross Sales-Labor.
        self.assertFalse(self._line(rec, "income").is_matched)
        self.assertEqual(rec.unmatched_count, 1)

    def test_10_undo_puts_the_category_back_and_drops_the_draft_entries(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        self._apply(rec)
        rec.action_cancel()

        self.assertEqual(rec.state, "cancel")
        self.assertEqual(self.patch.categ_id, self.categ_labor)
        self.assertFalse(rec.move_ids)

    def test_11_undo_is_refused_once_the_entry_is_posted(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        self._apply(rec)
        rec.move_ids.action_post()
        with self.assertRaises(UserError):
            rec.action_cancel()

    def test_12_applying_twice_is_refused(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        self._apply(rec)
        with self.assertRaises(UserError):
            rec.action_apply()

    def test_13_sales_outside_the_window_are_left_alone(self):
        self._sell(self.patch, 3, date="2026-06-15")
        self._sell(self.patch, 7, date="2026-07-15")
        rec = self._reclass(date_from="2026-07-01", date_to="2026-07-31")
        rec.action_compute()
        self.assertEqual(self._line(rec, "income").amount, -700.0)

    # ------------------------------------------------------------------
    # Closed periods: reversal + re-booking through a clearing account
    # ------------------------------------------------------------------

    def _close_june(self):
        self.company.sudo().fiscalyear_lock_date = "2026-06-30"
        self.env["ir.config_parameter"].sudo().set_param(
            "custom_retail_localization.categ_reclass_clearing_account_code", "CLEAR"
        )

    def test_14_closed_period_books_a_reversal_and_a_rebooking(self):
        self._sell(self.patch, 3, date="2026-06-15")
        self._close_june()
        rec = self._reclass()
        self._apply(rec)

        self.assertEqual(len(rec.move_ids), 2)
        # Neither entry may land back in the closed month.
        self.assertEqual(set(rec.move_ids.mapped("date")), {fields.Date.to_date("2026-08-03")})

        reversal = rec.move_ids.filtered(lambda m: "REVERSAL" in m.ref)
        rebooking = rec.move_ids.filtered(lambda m: "RE-BOOKING" in m.ref)
        self.assertEqual(len(reversal), 1)
        self.assertEqual(len(rebooking), 1)
        self.assertIn("2026-06", reversal.ref)

        # Reversal: Dr the account the wrong category credited, Cr clearing.
        self.assertEqual(reversal.line_ids.filtered(lambda line: line.debit).account_id, self.gs_labor)
        self.assertEqual(reversal.line_ids.filtered(lambda line: line.credit).account_id, self.clearing)
        # Re-booking: Dr clearing, Cr the account the right category would have.
        self.assertEqual(rebooking.line_ids.filtered(lambda line: line.debit).account_id, self.clearing)
        self.assertEqual(rebooking.line_ids.filtered(lambda line: line.credit).account_id, self.gs_misc)

        # The clearing account is a pass-through, never a resting place.
        clearing_lines = rec.move_ids.line_ids.filtered(lambda line: line.account_id == self.clearing)
        self.assertEqual(sum(clearing_lines.mapped("balance")), 0.0)
        # The OU rides on all four legs, not just the revenue ones.
        expected = {str(self.ou.id): 100.0}
        self.assertTrue(all(line.analytic_distribution == expected for line in rec.move_ids.line_ids))

    def test_15_closed_period_without_a_clearing_account_is_refused(self):
        self._sell(self.patch, 3, date="2026-06-15")
        self.company.sudo().fiscalyear_lock_date = "2026-06-30"
        self.env["ir.config_parameter"].sudo().set_param(
            "custom_retail_localization.categ_reclass_clearing_account_code", ""
        )
        rec = self._reclass()
        rec.action_compute()
        # Called at the booking level rather than through action_apply: with the
        # approval add-on installed the apply happens behind the gate, where the
        # engine deliberately swallows exceptions.
        with self.assertRaises(UserError) as caught:
            rec._book_closed_period(rec.line_ids)
        self.assertIn("categ_reclass_clearing_account_code", str(caught.exception))

    def test_16_open_and_closed_periods_are_booked_differently_in_one_run(self):
        self._sell(self.patch, 3, date="2026-06-15")  # closed
        self._sell(self.patch, 7, date="2026-07-15")  # open
        self._close_june()
        rec = self._reclass()
        self._apply(rec)

        july = rec.move_ids.filtered(lambda m: m.date == fields.Date.to_date("2026-07-15"))
        current = rec.move_ids.filtered(lambda m: m.date == fields.Date.to_date("2026-08-03"))
        self.assertEqual(len(july), 1)  # open period: one net entry on its own date
        self.assertEqual(len(current), 2)  # closed period: reversal + re-booking
        self.assertEqual(sum(july.line_ids.mapped("debit")), 700.0)
        self.assertEqual(sum(current.mapped("line_ids.debit")), 600.0)
        # The June entry never touches Gross Sales-misc directly; it goes via clearing.
        self.assertFalse(july.line_ids.filtered(lambda line: line.account_id == self.clearing))
