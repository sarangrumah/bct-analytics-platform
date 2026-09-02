# -*- coding: utf-8 -*-
"""Inbound QC tests — quarantine, release, unknown-item registration."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_inbound_qc")
class TestInboundQc(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The test runs as __system__ (uid 1), which is not base.user_admin and
        # therefore does not pick up the group granted in security.xml.
        cls.env.user.group_ids |= cls.env.ref("custom_wms_inbound_qc.group_wms_qc_manager")

        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.customer_loc = cls.env.ref("stock.stock_location_customers")

        cls.qc_loc = cls.env["stock.location"].create(
            {
                "name": "QC-HOLD",
                "usage": "internal",
                "location_id": cls.warehouse.view_location_id.id,
                "wms_is_qc_area": True,
                "wms_block_reservation": True,
            }
        )
        cls.qc_bin = cls.env["stock.location"].create(
            {"name": "QC-HOLD-01", "usage": "internal", "location_id": cls.qc_loc.id}
        )
        cls.free_bin = cls.env["stock.location"].create(
            {"name": "FREE-01", "usage": "internal", "location_id": cls.stock_loc.id}
        )
        cls.product = cls.env["product.product"].create({"name": "QC Widget", "type": "consu", "is_storable": True})

    # ------------------------------------------------------------------
    # Requirement 6 — quarantined stock is not reservable for outbound
    # ------------------------------------------------------------------

    def test_blocked_location_ids_include_children(self):
        """Flagging a parent quarantines every bin beneath it."""
        blocked = self.env["stock.location"]._wms_blocked_location_ids()
        self.assertIn(self.qc_loc.id, blocked)
        self.assertIn(self.qc_bin.id, blocked, "child bin must inherit the block")
        self.assertNotIn(self.free_bin.id, blocked)

    def test_quarantined_stock_is_not_available(self):
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(self.product, self.qc_bin, 10.0)
        available = Quant._get_available_quantity(self.product, self.warehouse.view_location_id)
        self.assertEqual(available, 0.0, "quarantined stock must not be reservable")

    def test_free_stock_is_available(self):
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(self.product, self.free_bin, 7.0)
        available = Quant._get_available_quantity(self.product, self.warehouse.view_location_id)
        self.assertEqual(available, 7.0)

    def test_bypass_context_sees_quarantined_stock(self):
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(self.product, self.qc_bin, 5.0)
        available = Quant.with_context(wms_allow_blocked_locations=True)._get_available_quantity(
            self.product, self.warehouse.view_location_id
        )
        self.assertEqual(available, 5.0, "the release transfer must still see the goods")

    def test_delivery_cannot_reserve_quarantined_stock(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.qc_bin, 10.0)
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.stock_loc.id,
                "location_dest_id": self.customer_loc.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 10.0,
                            "product_uom": self.product.uom_id.id,
                            "location_id": self.stock_loc.id,
                            "location_dest_id": self.customer_loc.id,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.action_assign()
        self.assertNotEqual(picking.state, "assigned", "delivery must not be reservable from quarantine")

    # ------------------------------------------------------------------
    # Requirement 2 — the QC gate
    # ------------------------------------------------------------------

    def _receipt(self, qty=6.0):
        ptype = self.warehouse.in_type_id
        ptype.write({"wms_qc_required": True, "wms_qc_location_id": self.qc_bin.id})
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": ptype.id,
                "location_id": self.env.ref("stock.stock_location_suppliers").id,
                "location_dest_id": self.qc_bin.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": qty,
                            "product_uom": self.product.uom_id.id,
                            "location_id": self.env.ref("stock.stock_location_suppliers").id,
                            "location_dest_id": self.qc_bin.id,
                        },
                    )
                ],
            }
        )
        return picking

    def test_receipt_starts_pending_qc(self):
        picking = self._receipt()
        self.assertEqual(picking.wms_qc_state, "pending")

    def test_qc_pass_creates_release_transfer_out_of_quarantine(self):
        picking = self._receipt(qty=6.0)
        picking.action_confirm()
        for line in picking.move_ids:
            line.quantity = 6.0
        picking.button_validate()
        self.assertEqual(picking.state, "done")

        picking.action_wms_qc_pass()
        self.assertEqual(picking.wms_qc_state, "passed")
        release = picking.wms_qc_release_picking_id
        self.assertTrue(release, "passing QC must create the release transfer")
        self.assertEqual(release.picking_type_id.code, "internal")
        self.assertEqual(release.move_ids.location_dest_id, self.stock_loc, "goods must land in pickable stock")
        self.assertEqual(release.state, "assigned", "release must reserve out of quarantine")

    def test_pending_receipt_is_not_a_putaway_event(self):
        """Auto-putaway must not walk goods straight past the QC gate.

        Regression: the engine rewrote the receipt's destination to a storage
        bin, so nothing ever landed in quarantine and the block held nothing.
        """
        strategy = self.env["custom.wms.putaway.strategy"].search(
            [("warehouse_id", "=", self.warehouse.id), ("active", "=", True)], limit=1
        )
        if not strategy:
            strategy = self.env["custom.wms.putaway.strategy"].create(
                {"name": "QC Test Strategy", "warehouse_id": self.warehouse.id, "rule_set": "custom"}
            )
        self.env["custom.wms.putaway.rule"].create(
            {
                "name": "send everything to FREE-01",
                "strategy_id": strategy.id,
                "tier": 1,
                "kind": "fixed_location",
                "target_location_id": self.free_bin.id,
            }
        )
        picking = self._receipt(qty=3.0)
        picking.action_confirm()
        picking.action_assign()
        self.assertTrue(picking.move_line_ids)
        self.assertNotEqual(
            picking.move_line_ids[0].location_dest_id,
            self.free_bin,
            "a QC-pending receipt must keep its quarantine destination",
        )

    def test_release_transfer_is_a_putaway_event(self):
        """The engine must slot the goods once QC releases them."""
        move_line = self.env["stock.move.line"]
        self.assertTrue(hasattr(move_line, "_is_incoming"))
        picking = self._receipt(qty=3.0)
        picking.action_confirm()
        for line in picking.move_ids:
            line.quantity = 3.0
        picking.button_validate()
        picking.action_wms_qc_pass()
        release = picking.wms_qc_release_picking_id
        self.assertTrue(release.move_line_ids)
        self.assertTrue(
            release.move_line_ids[0]._is_incoming(),
            "the release leg is where slotting happens",
        )

    def test_qc_pass_requires_validated_receipt(self):
        picking = self._receipt()
        with self.assertRaises(UserError):
            picking.action_wms_qc_pass()

    def test_qc_fail_leaves_goods_quarantined(self):
        picking = self._receipt(qty=4.0)
        picking.action_confirm()
        for line in picking.move_ids:
            line.quantity = 4.0
        picking.button_validate()
        picking.wms_qc_notes = "Seal broken on 2 cartons"
        picking.action_wms_qc_fail()
        self.assertEqual(picking.wms_qc_state, "failed")
        self.assertFalse(picking.wms_qc_release_picking_id)
        available = self.env["stock.quant"]._get_available_quantity(self.product, self.warehouse.view_location_id)
        self.assertEqual(available, 0.0)

    # ------------------------------------------------------------------
    # Requirement 2 — unknown-item registration
    # ------------------------------------------------------------------

    def test_capture_is_idempotent_per_receipt(self):
        Reg = self.env["custom.wms.product.registration"]
        picking = self._receipt()
        first = Reg.capture("8991234567890", picking=picking, quantity=2.0)
        second = Reg.capture("8991234567890", picking=picking, quantity=3.0)
        self.assertEqual(first, second, "re-scanning must accumulate, not duplicate")
        self.assertEqual(first.quantity, 5.0)

    def test_registration_approval_creates_product(self):
        Reg = self.env["custom.wms.product.registration"]
        reg = Reg.capture("8990000000017", description="Mystery carton")
        reg.write({"proposed_name": "Mystery Carton", "weight": 1.5})
        reg.action_submit()
        reg.action_approve()
        self.assertEqual(reg.state, "approved")
        self.assertTrue(reg.product_id)
        self.assertEqual(reg.product_id.barcode, "8990000000017")
        self.assertEqual(reg.product_id.weight, 1.5)

    def test_registration_rejects_duplicate_barcode(self):
        self.product.barcode = "8990000000024"
        Reg = self.env["custom.wms.product.registration"]
        reg = Reg.capture("8990000000024")
        reg.proposed_name = "Duplicate"
        reg.action_submit()
        with self.assertRaises(UserError):
            reg.action_approve()

    def test_submit_requires_a_name(self):
        reg = self.env["custom.wms.product.registration"].capture("8990000000031")
        with self.assertRaises(UserError):
            reg.action_submit()
