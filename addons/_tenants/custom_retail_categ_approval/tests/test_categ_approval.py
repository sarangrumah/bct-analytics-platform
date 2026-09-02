# -*- coding: utf-8 -*-
"""Guard + two-tier Finance approval on product category changes.

Fixtures mirror the apparel brand shape: COA-bucket root categories carrying the
Gross Sales / Sales Discount / Sales Return mapping, a store with its own
Operating Unit analytic, and POS orders standing in for what the retail import
posts.
"""

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestCategApproval(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.env.user.group_ids |= cls.env.ref("point_of_sale.group_pos_manager")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("retail_import.x24_discount_reclass", "1")
        icp.set_param("retail_import.x31_post_enabled", "0")

        Account = cls.env["account.account"]

        def income(name, code):
            return Account.create({"name": name, "code": code, "account_type": "income"})

        cls.gs_labor = income("Gross Sales-Labor (Service)", "AGSLAB")
        cls.gs_misc = income("Gross Sales-miscellaneous", "AGSMIS")
        cls.sd_labor = income("Sales discount-Labor (Service)", "ASDLAB")
        cls.sd_misc = income("Sales Discount-Miscellaneous", "ASDMIS")
        cls.sr_labor = income("Sales return-Labor (Service)", "ASRLAB")
        cls.sr_misc = income("Sales Return-miscellaneous", "ASRMIS")

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
        # Same COA bucket, one level down — moving between these must not be
        # blocked, it changes no account at all.
        cls.categ_labor_leaf = Categ.create({"name": "Alterations", "parent_id": cls.categ_labor.id})
        for categ in (cls.categ_labor_leaf,):
            categ.with_company(cls.company).property_account_income_categ_id = False

        cls.journal = cls.env["account.journal"].create(
            {"name": "General Journal", "type": "general", "code": "GLJVA", "company_id": cls.company.id}
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

        cls.patch = cls._make_product("Patches S", "TS1000413")
        cls.untouched = cls._make_product("Never Sold", "TS9999999")

        # Approvers. Tier 1 = Accounting Manager, tier 2 = the module's own
        # Finance Manager group, which ships empty.
        cls.accountant = cls._make_user("acc.manager", cls.env.ref("account.group_account_manager"))
        cls.finance = cls._make_user(
            "fin.manager", cls.env.ref("custom_retail_categ_approval.group_categ_reclass_finance_manager")
        )

    @classmethod
    def _make_product(cls, name, code):
        return cls.env["product.product"].create(
            {
                "name": name,
                "default_code": code,
                "type": "consu",
                "is_storable": False,
                "categ_id": cls.categ_labor.id,
                "available_in_pos": True,
                "lst_price": 100.0,
            }
        )

    @classmethod
    def _make_user(cls, login, group):
        user = cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "email": "%s@example.com" % login,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        user.group_ids |= group
        return user

    # ------------------------------------------------------------------
    def _sell(self, product, qty, date="2026-07-15"):
        session = self.env["pos.session"].search(
            [("config_id", "=", self.config.id), ("state", "!=", "closed")], limit=1
        )
        if not session:
            session = self.env["pos.session"].create({"config_id": self.config.id})
            session.update_stock_at_closing = False
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
                ],
            }
        )
        order.write({"state": "done"})
        return order

    def _reclass(self, product=None, categ=None):
        return self.env["retail.categ.reclass"].create(
            {
                "company_id": self.company.id,
                "product_tmpl_ids": [(6, 0, (product or self.patch).product_tmpl_id.ids)],
                "new_categ_id": (categ or self.categ_misc).id,
                "journal_id": self.journal.id,
                "fallback_date": "2026-08-03",
            }
        )

    # ------------------------------------------------------------------
    # The guard
    # ------------------------------------------------------------------

    def test_01_product_without_movement_can_be_recategorised_freely(self):
        self.untouched.product_tmpl_id.categ_id = self.categ_misc.id
        self.assertEqual(self.untouched.categ_id, self.categ_misc)

    def test_02_move_inside_the_same_coa_bucket_is_allowed(self):
        self._sell(self.patch, 3)
        # Alterations inherits Labor (Service)'s mapping, so no account moves.
        self.patch.product_tmpl_id.categ_id = self.categ_labor_leaf.id
        self.assertEqual(self.patch.categ_id, self.categ_labor_leaf)

    def test_03_move_to_another_bucket_after_a_sale_is_refused(self):
        self._sell(self.patch, 3)
        with self.assertRaises(UserError) as caught:
            self.patch.product_tmpl_id.categ_id = self.categ_misc.id
        self.assertIn("Product Category Reclassification", str(caught.exception))
        self.assertEqual(self.patch.categ_id, self.categ_labor)

    def test_04_the_sanctioned_path_is_not_blocked_by_its_own_guard(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        self._approve_fully(rec)
        self.assertEqual(self.patch.categ_id, self.categ_misc)

    # ------------------------------------------------------------------
    # The approval gate
    # ------------------------------------------------------------------

    def _request(self, rec):
        rec.action_apply()
        return rec.x_custom_approval_request_id

    def _approve_fully(self, rec):
        request = self._request(rec)
        request.with_user(self.accountant).action_approve()
        request.with_user(self.finance).action_approve()
        return request

    def test_05_applying_parks_the_record_and_changes_nothing(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._request(rec)

        self.assertEqual(rec.state, "to_approve")
        self.assertEqual(self.patch.categ_id, self.categ_labor)  # untouched
        self.assertFalse(rec.move_ids)
        self.assertEqual(request.state, "pending")
        self.assertEqual(request.res_model, "retail.categ.reclass")
        self.assertIn(self.accountant, request.pending_approver_ids)

    def test_06_pending_approvers_get_an_activity(self):
        # Pin the cap above whatever the host database's Accounting Manager
        # group happens to hold, so this asserts the fan-out path itself.
        self.env["ir.config_parameter"].sudo().set_param("custom_retail_categ_approval.activity_fanout_max", "500")
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._request(rec)

        todo = self.env.ref("mail.mail_activity_data_todo")
        activities = request.activity_ids.filtered(lambda a: a.activity_type_id == todo)
        self.assertIn(self.accountant, activities.mapped("user_id"))
        self.assertIn("Miscellaneous", activities[0].summary)

    def test_06b_a_crowded_tier_gets_a_chatter_note_not_a_to_do_each(self):
        # account.group_account_manager really does hold dozens of people on the
        # the apparel brand databases; forty ignorable to-dos is not a notification.
        self.env["ir.config_parameter"].sudo().set_param("custom_retail_categ_approval.activity_fanout_max", "0")
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._request(rec)

        todo = self.env.ref("mail.mail_activity_data_todo")
        self.assertFalse(request.activity_ids.filtered(lambda a: a.activity_type_id == todo))
        self.assertTrue(request.message_ids.filtered(lambda m: "too many for individual to-dos" in (m.body or "")))

    def test_07_tier_one_alone_is_not_enough(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._request(rec)

        request.with_user(self.accountant).action_approve()
        self.assertEqual(request.state, "pending")
        self.assertEqual(rec.state, "to_approve")
        self.assertEqual(self.patch.categ_id, self.categ_labor)
        self.assertIn(self.finance, request.pending_approver_ids)

    def test_08_final_approval_applies_the_change_and_books_the_correction(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._approve_fully(rec)

        self.assertEqual(request.state, "approved")
        self.assertEqual(rec.state, "applied")
        self.assertEqual(self.patch.categ_id, self.categ_misc)
        self.assertEqual(len(rec.move_ids), 1)
        self.assertEqual(sum(rec.move_ids.mapped("line_ids.debit")), 300.0)
        debit = rec.move_ids.line_ids.filtered(lambda line: line.debit)
        credit = rec.move_ids.line_ids.filtered(lambda line: line.credit)
        self.assertEqual(debit.account_id, self.gs_labor)
        self.assertEqual(credit.account_id, self.gs_misc)

    def test_09_rejection_leaves_everything_alone(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        request = self._request(rec)
        request.with_user(self.accountant).action_reject(comment="Wrong bucket")

        self.assertEqual(request.state, "rejected")
        self.assertEqual(self.patch.categ_id, self.categ_labor)
        self.assertFalse(rec.move_ids)

    def test_10_submitting_twice_does_not_open_a_second_request(self):
        self._sell(self.patch, 3)
        rec = self._reclass()
        rec.action_compute()
        first = self._request(rec)
        rec.action_apply()
        self.assertEqual(rec.x_custom_approval_request_id, first)
        self.assertEqual(
            self.env["approval.request"].search_count(
                [("res_model", "=", "retail.categ.reclass"), ("res_id", "=", rec.id)]
            ),
            1,
        )

    def test_11_a_product_with_nothing_posted_still_needs_approval(self):
        # No movement at all: no correction lines, but the category change itself
        # is still a Finance decision.
        rec = self._reclass(product=self.untouched)
        rec.action_compute()
        self.assertFalse(rec.line_ids)
        self._approve_fully(rec)
        self.assertEqual(self.untouched.categ_id, self.categ_misc)
