# -*- coding: utf-8 -*-
"""Transfer-order engine tests."""

from __future__ import annotations

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_to_engine")
class TestToEngine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.src_loc = cls.env["stock.location"].create(
            {
                "name": "TO-SRC",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
            }
        )
        cls.tgt_loc = cls.env["stock.location"].create(
            {
                "name": "TO-TGT",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "TO Widget",
                "type": "consu",
                "is_storable": True,
            }
        )

    def test_low_water_creates_proposal(self):
        # Seed: source low (2 units), target has stock (10 units), threshold = 5
        self.env["stock.quant"]._update_available_quantity(self.product, self.src_loc, 2.0)
        self.env["stock.quant"]._update_available_quantity(self.product, self.tgt_loc, 10.0)
        # NB: trigger semantics — source is where we replenish TO, donor is the
        # "target_location_domain". Build a rule using the inverse domains.
        rule = self.env["custom.to.rule"].create(
            {
                "name": "low water",
                "trigger": "low_water_mark",
                "source_location_domain": "[('location_id','=',%d)]" % self.src_loc.id,
                "target_location_domain": "[('location_id','=',%d)]" % self.tgt_loc.id,
                "low_water_qty": 5.0,
            }
        )
        engine = self.env["custom.to.engine"]
        proposals = engine.evaluate_rule(rule)
        self.assertTrue(proposals, "Low-water rule should yield a proposal")
        self.assertEqual(proposals[0]["product_id"], self.product.id)

    def test_low_water_never_proposes_a_bin_to_itself(self):
        # Overlapping domains: the same bin is both below the mark and a donor.
        self.env["stock.quant"]._update_available_quantity(self.product, self.src_loc, 2.0)
        both = "[('location_id','in',[%d,%d])]" % (self.src_loc.id, self.tgt_loc.id)
        rule = self.env["custom.to.rule"].create(
            {
                "name": "overlapping domains",
                "trigger": "low_water_mark",
                "source_location_domain": both,
                "target_location_domain": both,
                "low_water_qty": 5.0,
            }
        )
        proposals = self.env["custom.to.engine"].evaluate_rule(rule)
        self.assertFalse(
            [p for p in proposals if p["source_location_id"] == p["target_location_id"]],
            "a transfer from a bin to itself replenishes nothing",
        )

    def test_manual_wizard_creates_to_and_move(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.src_loc, 10.0)
        wiz = self.env["custom.transfer.order.manual.wizard"].create(
            {
                "product_id": self.product.id,
                "source_location_id": self.src_loc.id,
                "target_location_id": self.tgt_loc.id,
                "qty": 3.0,
            }
        )
        action = wiz.action_create()
        to = self.env["custom.transfer.order"].browse(action["res_id"])
        self.assertTrue(to.exists())
        self.assertTrue(to.stock_move_id)
        self.assertEqual(to.stock_move_id.product_uom_qty, 3.0)

    def test_rule_skips_when_source_empty(self):
        rule = self.env["custom.to.rule"].create(
            {
                "name": "empty src",
                "trigger": "low_water_mark",
                "source_location_domain": "[('location_id','=',%d)]" % self.src_loc.id,
                "target_location_domain": "[('location_id','=',%d)]" % self.tgt_loc.id,
                "low_water_qty": 5.0,
            }
        )
        engine = self.env["custom.to.engine"]
        proposals = engine.evaluate_rule(rule)
        self.assertEqual(proposals, [])

    def test_materialize_creates_stock_move(self):
        engine = self.env["custom.to.engine"]
        move = engine.materialize(
            {
                "source_location_id": self.src_loc.id,
                "target_location_id": self.tgt_loc.id,
                "product_id": self.product.id,
                "planned_qty": 4.0,
                "name": "T-mat",
                "company_id": self.env.company.id,
            }
        )
        self.assertEqual(move.product_uom_qty, 4.0)
        self.assertEqual(move.location_id, self.src_loc)
        self.assertEqual(move.location_dest_id, self.tgt_loc)

    def test_manual_trigger_returns_empty(self):
        rule = self.env["custom.to.rule"].create(
            {
                "name": "manual-rule",
                "trigger": "manual",
            }
        )
        engine = self.env["custom.to.engine"]
        self.assertEqual(engine.evaluate_rule(rule), [])

    def test_expiry_trigger_safe_without_lots(self):
        # No lots seeded; expect empty (not crash) even when scrap exists.
        rule = self.env["custom.to.rule"].create(
            {
                "name": "expiry",
                "trigger": "expiry_approaching",
                "source_location_domain": "[('location_id','=',%d)]" % self.src_loc.id,
                "expiry_days_ahead": 7,
            }
        )
        engine = self.env["custom.to.engine"]
        proposals = engine.evaluate_rule(rule)
        self.assertIsInstance(proposals, list)
