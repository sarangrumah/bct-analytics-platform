# -*- coding: utf-8 -*-
"""Putaway engine tests.

Note the scoring contract: ``_score_rule`` returns a 3-tuple
``(score, reason, location)``. Handlers that choose among candidates return the
bin they picked in the third slot; the engine no longer discards it.
"""

from __future__ import annotations

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_putaway")
class TestPutaway(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.supplier_loc = cls.env.ref("stock.stock_location_suppliers")

        cls.loc_a = cls.env["stock.location"].create(
            {
                "name": "BIN-A",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
                "volume_capacity_m3": 10.0,
                "wms_walk_sequence": 10,
            }
        )
        cls.loc_b = cls.env["stock.location"].create(
            {
                "name": "BIN-B",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
                "volume_capacity_m3": 0.001,
                "wms_walk_sequence": 90,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Widget",
                "type": "consu",
                "is_storable": True,
                "volume": 0.5,
                "weight": 1.0,
                "abc_class": "A",
            }
        )
        cls.strategy = cls.env["custom.wms.putaway.strategy"].create(
            {
                "name": "Test Strategy",
                "warehouse_id": cls.warehouse.id,
                "rule_set": "custom",
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_move_line(self, qty=2.0, product=None):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "location_id": self.supplier_loc.id,
                "location_dest_id": self.stock_loc.id,
            }
        )
        return self.env["stock.move.line"].create(
            {
                "picking_id": picking.id,
                "product_id": (product or self.product).id,
                "location_id": self.supplier_loc.id,
                "location_dest_id": self.stock_loc.id,
                "quantity": qty,
            }
        )

    def _rule(self, **vals):
        vals.setdefault("strategy_id", self.strategy.id)
        vals.setdefault("tier", 1)
        vals.setdefault("name", vals.get("kind", "rule"))
        return self.env["custom.wms.putaway.rule"].create(vals)

    # ------------------------------------------------------------------
    # Existing behaviour
    # ------------------------------------------------------------------

    def test_fixed_location_strategy(self):
        rule = self._rule(kind="fixed_location", target_location_id=self.loc_a.id)
        score, reason, location = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertEqual(score, 100)
        self.assertIn("Fixed", reason)
        self.assertEqual(location, self.loc_a)

    def test_propose_for_product_ranks_without_a_move_line(self):
        """The handheld's stock-check screen asks for a bin with no transfer
        behind it — same ranking, warehouse passed in instead of derived."""
        self._rule(kind="fixed_location", target_location_id=self.loc_a.id)
        engine = self.env["custom.putaway.engine"]
        proposals = engine.propose_for_product(self.product, self.warehouse)
        self.assertTrue(proposals)
        self.assertEqual(proposals[0]["location_id"], self.loc_a.id)
        self.assertEqual(proposals[0]["score"], 100)
        # And it agrees with what a real move line would have produced.
        self.assertEqual(
            [p["location_id"] for p in proposals],
            [p["location_id"] for p in engine.propose(self._make_move_line(qty=1.0))],
        )

    def test_propose_for_product_needs_a_warehouse(self):
        self._rule(kind="fixed_location", target_location_id=self.loc_a.id)
        engine = self.env["custom.putaway.engine"]
        empty = self.env["stock.warehouse"].browse()
        self.assertEqual(engine.propose_for_product(self.product, empty), [])
        self.assertEqual(engine.propose_for_product(self.env["product.product"].browse(), self.warehouse), [])

    def test_nearest_empty_strategy(self):
        rule = self._rule(
            kind="nearest_empty",
            target_location_domain="[('id','in',[%d,%d])]" % (self.loc_a.id, self.loc_b.id),
        )
        score, _reason, location = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertGreater(score, 0)
        self.assertTrue(location)

    def test_by_volume_rejects_oversized(self):
        rule = self._rule(kind="by_volume", target_location_id=self.loc_b.id)
        score, reason, _loc = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line(qty=5.0))
        self.assertEqual(score, 0)
        self.assertIn("Oversized", reason)

    def test_by_abc_velocity_a_places_near_dock(self):
        rule = self._rule(kind="by_abc_velocity", abc_class="A", target_location_id=self.loc_a.id)
        score, _r, _loc = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertGreaterEqual(score, 90)

    def test_custom_python_safe_eval_rejects_unsafe(self):
        rule = self._rule(kind="custom_python", custom_python="__import__('os').system('echo pwned')")
        score, reason, _loc = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertEqual(score, 0)
        self.assertTrue("unsafe" in reason or "rejected" in reason or reason == "")

    def test_custom_python_valid_returns_score(self):
        rule = self._rule(kind="custom_python", custom_python="(False, 50)")
        score, _r, _loc = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertEqual(score, 50)

    # ------------------------------------------------------------------
    # Pinned rules must not widen to the whole warehouse
    # ------------------------------------------------------------------

    def test_pinned_rule_does_not_widen_to_all_bins(self):
        """A rule with only target_location_id must consider exactly that bin."""
        rule = self._rule(kind="by_volume", target_location_id=self.loc_b.id)
        cands = self.env["custom.putaway.engine"]._rule_candidates(rule, self._make_move_line(qty=0.001))
        self.assertEqual(cands, self.loc_b)

    # ------------------------------------------------------------------
    # Requirement 5 — nearest empty picks the CLOSEST bin, not the first
    # ------------------------------------------------------------------

    def test_nearest_empty_prefers_closest_walk_sequence(self):
        dock = self.env["stock.location"].create(
            {
                "name": "DOCK",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_walk_sequence": 1,
            }
        )
        rule = self._rule(
            kind="nearest_empty",
            dock_location_id=dock.id,
            target_location_domain="[('id','in',[%d,%d])]" % (self.loc_b.id, self.loc_a.id),
        )
        _score, _reason, location = self.env["custom.putaway.engine"]._score_rule(rule, self._make_move_line())
        self.assertEqual(location, self.loc_a, "BIN-A (walk 10) is closer to the dock than BIN-B (walk 90)")

    def test_nearest_empty_skips_occupied_bins(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.loc_a, 3.0)
        rule = self._rule(
            kind="nearest_empty",
            target_location_domain="[('id','in',[%d,%d])]" % (self.loc_a.id, self.loc_b.id),
        )
        _score, _reason, location = self.env["custom.putaway.engine"]._score_rule(
            rule, self._make_move_line(qty=0.0001)
        )
        self.assertEqual(location, self.loc_b, "an occupied bin is not empty")

    # ------------------------------------------------------------------
    # Requirement 3 — dimension (PxLxT) and weight
    # ------------------------------------------------------------------

    def _dimensioned_setup(self):
        ptype = self.env["stock.package.type"].create(
            {
                "name": "Carton M",
                "packaging_length": 400.0,
                "width": 300.0,
                "height": 200.0,
                "base_weight": 0.4,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "Boxed Widget",
                "type": "consu",
                "is_storable": True,
                "weight": 2.0,
                "wms_package_type_id": ptype.id,
                "wms_units_per_package": 10.0,
            }
        )
        small = self.env["stock.location"].create(
            {
                "name": "SMALL-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_length_mm": 200.0,
                "wms_width_mm": 200.0,
                "wms_height_mm": 150.0,
            }
        )
        exact = self.env["stock.location"].create(
            {
                "name": "EXACT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_length_mm": 420.0,
                "wms_width_mm": 320.0,
                "wms_height_mm": 220.0,
            }
        )
        roomy = self.env["stock.location"].create(
            {
                "name": "ROOMY-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_length_mm": 1200.0,
                "wms_width_mm": 1000.0,
                "wms_height_mm": 900.0,
            }
        )
        return ptype, product, small, exact, roomy

    def test_dimension_fit_rejects_too_small_bin(self):
        _pt, product, small, _exact, _roomy = self._dimensioned_setup()
        engine = self.env["custom.putaway.engine"]
        self.assertFalse(
            engine._fits_dimensions(small, product._wms_package_dims_mm()),
            "a 400x300x200 carton cannot go into a 200x200x150 bin",
        )

    def test_dimension_fit_allows_rotation_in_the_plane(self):
        engine = self.env["custom.putaway.engine"]
        loc = self.env["stock.location"].create(
            {
                "name": "ROT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_length_mm": 300.0,
                "wms_width_mm": 500.0,
                "wms_height_mm": 300.0,
            }
        )
        # 400 long x 250 wide fits only if the package may be turned 90 degrees.
        self.assertTrue(engine._fits_dimensions(loc, (400.0, 250.0, 200.0)))

    def test_dimension_unknown_geometry_is_permissive(self):
        """Dimensions are an optional refinement — absent data must not block."""
        engine = self.env["custom.putaway.engine"]
        self.assertTrue(engine._fits_dimensions(self.loc_a, (0.0, 0.0, 0.0)))
        self.assertTrue(engine._fits_dimensions(self.loc_a, (100.0, 100.0, 100.0)))

    def test_by_dimension_prefers_the_snuggest_bin(self):
        _pt, product, _small, exact, _roomy = self._dimensioned_setup()
        rule = self._rule(
            kind="by_dimension",
            target_location_domain="[('usage','=','internal')]",
        )
        _score, _reason, location = self.env["custom.putaway.engine"]._score_rule(
            rule, self._make_move_line(qty=10.0, product=product)
        )
        self.assertEqual(location, exact, "the tightest fitting bin should win over the roomy one")

    def test_weight_ceiling_blocks_a_bin(self):
        heavy = self.env["product.product"].create(
            {"name": "Anvil", "type": "consu", "is_storable": True, "weight": 100.0}
        )
        light_bin = self.env["stock.location"].create(
            {
                "name": "LIGHT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_max_weight_kg": 50.0,
            }
        )
        engine = self.env["custom.putaway.engine"]
        ml = self._make_move_line(qty=1.0, product=heavy)
        self.assertFalse(engine._feasible_locations(light_bin, ml), "100 kg cannot go into a 50 kg bin")

    def test_storage_category_max_weight_wins_over_fallback(self):
        category = self.env["stock.storage.category"].create({"name": "Light Shelf", "max_weight": 5.0})
        loc = self.env["stock.location"].create(
            {
                "name": "CAT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "storage_category_id": category.id,
                "wms_max_weight_kg": 999.0,
            }
        )
        self.assertEqual(loc._wms_effective_max_weight(), 5.0)

    def test_gross_weight_includes_handling_unit_tare(self):
        _pt, product, _s, _e, _r = self._dimensioned_setup()
        # 10 units = 1 carton -> 10*2.0 kg net + 1*0.4 kg tare
        self.assertAlmostEqual(product._wms_gross_weight_kg(10.0), 20.4, places=3)

    # ------------------------------------------------------------------
    # Requirement 4 — category reservation
    # ------------------------------------------------------------------

    def test_reserved_bin_rejects_foreign_category(self):
        footwear = self.env["product.category"].create({"name": "Footwear TEST"})
        apparel = self.env["product.category"].create({"name": "Apparel TEST"})
        bin_foot = self.env["stock.location"].create(
            {
                "name": "FOOT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_allowed_categ_ids": [(6, 0, [footwear.id])],
            }
        )
        shoe = self.env["product.product"].create(
            {"name": "Shoe", "type": "consu", "is_storable": True, "categ_id": footwear.id}
        )
        shirt = self.env["product.product"].create(
            {"name": "Shirt", "type": "consu", "is_storable": True, "categ_id": apparel.id}
        )
        engine = self.env["custom.putaway.engine"]
        self.assertTrue(engine._feasible_locations(bin_foot, self._make_move_line(product=shoe)))
        self.assertFalse(engine._feasible_locations(bin_foot, self._make_move_line(product=shirt)))

    def test_category_reservation_is_inherited_by_children(self):
        parent = self.env["product.category"].create({"name": "Sport TEST"})
        child = self.env["product.category"].create({"name": "Running TEST", "parent_id": parent.id})
        bin_sport = self.env["stock.location"].create(
            {
                "name": "SPORT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_allowed_categ_ids": [(6, 0, [parent.id])],
            }
        )
        self.assertTrue(bin_sport._wms_accepts_category(child), "reserving a parent reserves the tree")

    def test_unreserved_bin_accepts_everything(self):
        categ = self.env["product.category"].create({"name": "Whatever TEST"})
        self.assertTrue(self.loc_a._wms_accepts_category(categ))

    def test_enforced_reservation_blocks_manual_move_line(self):
        footwear = self.env["product.category"].create({"name": "Footwear ENF"})
        strict_bin = self.env["stock.location"].create(
            {
                "name": "STRICT-BIN",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "wms_allowed_categ_ids": [(6, 0, [footwear.id])],
                "wms_enforce_categ": True,
            }
        )
        ml = self._make_move_line()
        with self.assertRaises(ValidationError):
            ml.location_dest_id = strict_bin.id

    # ------------------------------------------------------------------
    # Threshold is configurable
    # ------------------------------------------------------------------

    def test_auto_apply_threshold_defaults_to_90(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_putaway.auto_apply_threshold", "")
        self.assertEqual(self.env["custom.putaway.engine"]._auto_apply_threshold(), 90)

    def test_auto_apply_threshold_is_configurable(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_putaway.auto_apply_threshold", "40")
        self.assertEqual(self.env["custom.putaway.engine"]._auto_apply_threshold(), 40)

    def test_auto_apply_threshold_survives_garbage(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_putaway.auto_apply_threshold", "not-a-number")
        self.assertEqual(self.env["custom.putaway.engine"]._auto_apply_threshold(), 90)

    def test_low_score_does_not_auto_apply(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_putaway.auto_apply_threshold", "99")
        self._rule(kind="by_abc_velocity", abc_class="A", target_location_id=self.loc_a.id)
        ml = self._make_move_line()
        suggestion = self.env["custom.putaway.engine"].apply_top_proposal(ml)
        self.assertTrue(suggestion)
        self.assertEqual(suggestion.status, "pending", "score 95 must not clear a threshold of 99")

    def test_high_score_auto_applies(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_putaway.auto_apply_threshold", "90")
        self._rule(kind="fixed_location", target_location_id=self.loc_a.id)
        ml = self._make_move_line()
        suggestion = self.env["custom.putaway.engine"].apply_top_proposal(ml)
        self.assertEqual(suggestion.status, "applied")
        self.assertEqual(ml.location_dest_id, self.loc_a)

    # ------------------------------------------------------------------
    # Round robin actually rotates
    # ------------------------------------------------------------------

    def test_zone_round_robin_rotates_across_bins(self):
        rule = self._rule(
            kind="zone_round_robin",
            target_location_domain="[('id','in',[%d,%d])]" % (self.loc_a.id, self.loc_b.id),
        )
        engine = self.env["custom.putaway.engine"]
        # Build the move lines FIRST. Creating one fires the auto-proposal hook,
        # which itself advances the rotation cursor — interleaving creation with
        # scoring would advance it twice per iteration and mask the rotation.
        lines = [self._make_move_line(qty=0.0001) for _ in range(4)]
        picked = {engine._score_rule(rule, ml)[2].id for ml in lines}
        self.assertEqual(len(picked), 2, "round robin must spread across both bins")

    def test_round_robin_cursor_advances_and_wraps(self):
        rule = self._rule(kind="zone_round_robin", target_location_id=self.loc_a.id)
        self.assertEqual(rule._next_round_robin_index(3), 0)
        self.assertEqual(rule._next_round_robin_index(3), 1)
        self.assertEqual(rule._next_round_robin_index(3), 2)
        self.assertEqual(rule._next_round_robin_index(3), 0, "cursor must wrap")
        self.assertEqual(rule._next_round_robin_index(0), 0, "empty candidate set is safe")
