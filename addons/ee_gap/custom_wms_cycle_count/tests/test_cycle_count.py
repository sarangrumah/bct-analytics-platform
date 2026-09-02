# -*- coding: utf-8 -*-
"""Cycle count tests."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_cycle_count")
class TestCycleCount(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.loc = cls.env["stock.location"].create(
            {
                "name": "BIN-CC",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "CC Widget",
                "type": "consu",
                "is_storable": True,
                "abc_class": "A",
                "standard_price": 10.0,
            }
        )
        cls.env["stock.quant"]._update_available_quantity(cls.product, cls.loc, 20.0)

        cls.plan = cls.env["custom.cycle.count.plan"].create(
            {
                "name": "Plan ABC",
                "warehouse_id": cls.warehouse.id,
                "frequency": "weekly",
                "method": "abc_velocity",
                "target_count_per_period": 5,
            }
        )

    def _start_session(self, method=None):
        if method:
            self.plan.method = method
        wiz = self.env["custom.cycle.count.start.wizard"].create(
            {
                "plan_id": self.plan.id,
            }
        )
        wiz.action_start()
        return self.env["custom.cycle.count.session"].search([("plan_id", "=", self.plan.id)], limit=1, order="id desc")

    def test_plan_generates_seed_lines(self):
        session = self._start_session()
        self.assertTrue(session)
        self.assertGreaterEqual(len(session.line_ids), 1)
        self.assertEqual(session.state, "draft")

    def test_abc_velocity_seeding_prefers_a_class(self):
        session = self._start_session(method="abc_velocity")
        a_products = session.line_ids.filtered(lambda l: l.product_id.abc_class == "A")
        self.assertTrue(a_products, "ABC velocity should pick at least one A-class product")

    def test_count_flow_computes_variance(self):
        session = self._start_session()
        session.action_start()
        line = session.line_ids[:1]
        line.action_count(qty=15.0)
        self.assertEqual(line.status, "counted")
        self.assertEqual(line.variance_qty, 15.0 - line.expected_qty)
        # Zero division safety: pct is 0 when expected is 0
        line.expected_qty = 0
        line._compute_variance()
        self.assertEqual(line.variance_pct, 0.0)

    def test_supervisor_approval_gate(self):
        session = self._start_session()
        session.action_start()
        line = session.line_ids[:1]
        line.action_count(qty=18.0)
        # Strip supervisor group from current user
        sup_group = self.env.ref("custom_wms_cycle_count.group_cycle_count_supervisor")
        self.env.user.group_ids = [(3, sup_group.id)]
        with self.assertRaises(UserError):
            line.action_approve()
        # Grant and retry
        self.env.user.group_ids = [(4, sup_group.id)]
        line.action_approve()
        self.assertEqual(line.status, "approved")

    def test_new_item_flag_captures_name(self):
        session = self._start_session()
        line = self.env["custom.cycle.count.line"].create(
            {
                "session_id": session.id,
                "location_id": self.loc.id,
                "is_new_item": True,
                "new_item_product_temp_name": "MysteryBox-2099",
                "counted_qty": 3.0,
            }
        )
        self.assertTrue(line.is_new_item)
        self.assertEqual(line.new_item_product_temp_name, "MysteryBox-2099")

    def test_adjustment_post_actually_moves_stock(self):
        """Posting a variance must reconcile the ledger, not just flip a flag.

        Two regressions guarded here:
          * Odoo 19 removed ``stock.move.name`` — creating the move with it
            raised ValueError and every adjustment failed to post.
          * The replacement created the move but never completed it, so
            ``posted`` read True while on-hand was untouched — a silent no-op,
            which is worse than the crash it replaced.
        """
        sup_group = self.env.ref("custom_wms_cycle_count.group_cycle_count_supervisor")
        self.env.user.group_ids = [(4, sup_group.id)]

        session = self._start_session()
        line = session.line_ids.filtered(lambda l: l.product_id == self.product)[:1]
        self.assertTrue(line, "the probe product must be in the session")
        self.assertEqual(line.expected_qty, 20.0)

        line.action_count(14.0)  # 20 on hand, counted 14 -> variance -6
        line.action_approve()

        adjustment = self.env["custom.cycle.count.adjustment"].search([("line_id", "=", line.id)], limit=1)
        self.assertTrue(adjustment, "approving a non-zero variance must create an adjustment")
        adjustment.action_post()

        self.assertTrue(adjustment.posted)
        self.assertEqual(adjustment.stock_move_id.state, "done", "the move must be completed, not left draft")
        self.assertIn(session.name, adjustment.stock_move_id.reference or "")

        on_hand = sum(
            self.env["stock.quant"]
            .search([("location_id", "=", self.loc.id), ("product_id", "=", self.product.id)])
            .mapped("quantity")
        )
        self.assertEqual(on_hand, 14.0, "on-hand must match the counted quantity after posting")

    def test_adjustment_post_is_idempotent(self):
        sup_group = self.env.ref("custom_wms_cycle_count.group_cycle_count_supervisor")
        self.env.user.group_ids = [(4, sup_group.id)]
        session = self._start_session()
        line = session.line_ids.filtered(lambda l: l.product_id == self.product)[:1]
        line.action_count(18.0)
        line.action_approve()
        adjustment = self.env["custom.cycle.count.adjustment"].search([("line_id", "=", line.id)], limit=1)
        adjustment.action_post()
        move = adjustment.stock_move_id
        adjustment.action_post()  # second call must be a no-op
        self.assertEqual(adjustment.stock_move_id, move, "re-posting must not create a second move")
