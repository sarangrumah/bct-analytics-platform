# -*- coding: utf-8 -*-
"""Integration tests for action_apply_to_picking — qty_done + lot handling."""

from odoo.tests.common import TransactionCase


class TestApplyPicking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env["custom.barcode.scan.session"]
        cls.ScanLine = cls.env["custom.barcode.scan.line"]
        cls.Picking = cls.env["stock.picking"]
        cls.PickingType = cls.env.ref("stock.picking_type_out")
        cls.Product = cls.env["product.product"]
        cls.Location = cls.env["stock.location"]
        cls.Lot = cls.env["stock.lot"]

        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.customer_loc = cls.env.ref("stock.stock_location_customers")

        cls.product = cls.Product.create(
            {
                "name": "BC Test Product",
                "type": "consu",
                "is_storable": True,
                "barcode": "BC-TEST-001",
                "tracking": "none",
            }
        )
        cls.product_lot = cls.Product.create(
            {
                "name": "BC Test Lot Product",
                "type": "consu",
                "is_storable": True,
                "barcode": "BC-TEST-002",
                "tracking": "lot",
            }
        )

    def _make_picking(self, product, qty):
        picking = self.Picking.create(
            {
                "picking_type_id": self.PickingType.id,
                "location_id": self.stock_loc.id,
                "location_dest_id": self.customer_loc.id,
            }
        )
        self.env["stock.move"].create(
            {
                # Odoo 19 removed stock.move.name — passing it raises
                # ValueError: Invalid field 'name' in 'stock.move'.
                "product_id": product.id,
                "product_uom_qty": qty,
                "product_uom": product.uom_id.id,
                "picking_id": picking.id,
                "location_id": self.stock_loc.id,
                "location_dest_id": self.customer_loc.id,
            }
        )
        picking.action_confirm()
        return picking

    def test_apply_updates_move_line_qty(self):
        picking = self._make_picking(self.product, 5.0)
        session = self.Session.create({"picking_id": picking.id, "state": "scanning"})
        # Two scans of 2 each — should end up qty_done = 4.0
        for _ in range(2):
            self.ScanLine.create(
                {
                    "session_id": session.id,
                    "product_id": self.product.id,
                    "raw_barcode": "BC-TEST-001",
                    "quantity": 2.0,
                    "status": "ok",
                }
            )
        session.action_apply_to_picking()

        move_lines = picking.move_line_ids.filtered(lambda m: m.product_id.id == self.product.id)
        self.assertTrue(move_lines)
        qty_field = "qty_done" if "qty_done" in move_lines._fields else "quantity"
        total = sum(getattr(m, qty_field) or 0.0 for m in move_lines)
        self.assertAlmostEqual(total, 4.0, places=3)

    def test_apply_creates_lot_for_tracked_product(self):
        picking = self._make_picking(self.product_lot, 3.0)
        session = self.Session.create({"picking_id": picking.id, "state": "scanning"})
        self.ScanLine.create(
            {
                "session_id": session.id,
                "product_id": self.product_lot.id,
                "raw_barcode": "NEW-LOT-XYZ",
                "quantity": 3.0,
                "status": "ok",
            }
        )
        session.action_apply_to_picking()

        # A new lot named NEW-LOT-XYZ should now exist and be attached.
        lot = self.Lot.search(
            [
                ("name", "=", "NEW-LOT-XYZ"),
                ("product_id", "=", self.product_lot.id),
            ],
            limit=1,
        )
        self.assertTrue(lot, "Expected a new stock.lot to be created from the scan")
        ml = picking.move_line_ids.filtered(lambda m: m.product_id.id == self.product_lot.id)
        self.assertTrue(ml)
        self.assertEqual(ml[:1].lot_id.id, lot.id)

    def test_apply_no_picking_is_noop(self):
        session = self.Session.create({"state": "scanning"})
        self.ScanLine.create(
            {
                "session_id": session.id,
                "product_id": self.product.id,
                "raw_barcode": "BC-TEST-001",
                "quantity": 1.0,
                "status": "ok",
            }
        )
        # Should not raise — just logs and returns True.
        self.assertTrue(session.action_apply_to_picking())

    def test_summary_helper_returns_rows(self):
        picking = self._make_picking(self.product, 5.0)
        session = self.Session.create({"picking_id": picking.id, "state": "scanning"})
        self.ScanLine.create(
            {
                "session_id": session.id,
                "product_id": self.product.id,
                "raw_barcode": "BC-TEST-001",
                "quantity": 3.0,
                "status": "ok",
            }
        )
        rows = session.get_picking_summary_data()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product"].id, self.product.id)
        self.assertAlmostEqual(rows[0]["expected"], 5.0, places=3)
        self.assertAlmostEqual(rows[0]["scanned"], 3.0, places=3)
        # Deviation = (3-5)/5 * 100 = -40%
        self.assertAlmostEqual(rows[0]["deviation"], -40.0, places=2)
