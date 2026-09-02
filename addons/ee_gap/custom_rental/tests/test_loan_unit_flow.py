# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError, ValidationError


@tagged("post_install", "-at_install")
class TestLoanUnitFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("custom_rental.config_stock_integration", "True")
        cls.partner = cls.env["res.partner"].create({"name": "Drone Renter"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "Drone X",
                "type": "consu",
                "is_storable": True,
            }
        )
        cls.asset = cls.env["rental.asset"].create(
            {
                "name": "Drone Asset Serial 1",
                "code": "DRN-S1",
                "daily_rate": 100.0,
                "product_id": cls.product.id,
            }
        )
        cls.now = datetime(2026, 6, 1, 9, 0)

    def _make_bulk(self, qty=400, loan_qty=100, days=7):
        return self.env["rental.order"].create(
            {
                "partner_id": self.partner.id,
                "product_id": self.product.id,
                "qty": qty,
                "loan_qty": loan_qty,
                "pickup_dt": self.now,
                "return_dt_expected": self.now + timedelta(days=days),
                "daily_rate": 100.0,
            }
        )

    def _make_serial(self, days=2):
        return self.env["rental.order"].create(
            {
                "partner_id": self.partner.id,
                "asset_id": self.asset.id,
                "pickup_dt": self.now,
                "return_dt_expected": self.now + timedelta(days=days),
                "daily_rate": 100.0,
            }
        )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    def test_constraint_mode_required(self):
        with self.assertRaises(ValidationError):
            self.env["rental.order"].create(
                {
                    "partner_id": self.partner.id,
                    "pickup_dt": self.now,
                    "return_dt_expected": self.now + timedelta(days=1),
                    "daily_rate": 100.0,
                }
            )

    def test_constraint_serial_qty_must_be_one(self):
        with self.assertRaises(ValidationError):
            self.env["rental.order"].create(
                {
                    "partner_id": self.partner.id,
                    "asset_id": self.asset.id,
                    "qty": 5,
                    "pickup_dt": self.now,
                    "return_dt_expected": self.now + timedelta(days=1),
                    "daily_rate": 100.0,
                }
            )

    def test_constraint_loan_qty_non_negative(self):
        order = self._make_bulk(qty=10, loan_qty=0)
        with self.assertRaises(ValidationError):
            order.loan_qty = -1

    # ------------------------------------------------------------------
    # Fees
    # ------------------------------------------------------------------
    def test_fees_skip_loan_qty(self):
        order = self._make_bulk(qty=400, loan_qty=100, days=7)
        # rental_fee = daily_rate * qty * days_planned = 100 * 400 * 7
        # NOT 100 * 500 * 7
        self.assertEqual(order.rental_fee, 100.0 * 400 * 7)

    # ------------------------------------------------------------------
    # Picking creation
    # ------------------------------------------------------------------
    def test_serial_mode_creates_single_move(self):
        if not self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1):
            self.skipTest("no outgoing picking type available")
        order = self._make_serial()
        order.action_confirm()
        self.assertTrue(order.pickup_picking_id)
        moves = order.pickup_picking_id.move_ids
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves.product_uom_qty, 1.0)
        self.assertFalse(moves.is_loan)

    def test_bulk_mode_creates_two_moves(self):
        if not self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1):
            self.skipTest("no outgoing picking type available")
        order = self._make_bulk(qty=400, loan_qty=100)
        order.action_confirm()
        self.assertTrue(order.pickup_picking_id)
        moves = order.pickup_picking_id.move_ids
        self.assertEqual(len(moves), 2)
        main = moves.filtered(lambda m: not m.is_loan)
        loan = moves.filtered("is_loan")
        self.assertEqual(main.product_uom_qty, 400.0)
        self.assertEqual(loan.product_uom_qty, 100.0)
        self.assertTrue(loan.name.startswith("[LOAN]"))

    def test_bulk_mode_no_loan_creates_single_move(self):
        if not self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1):
            self.skipTest("no outgoing picking type available")
        order = self._make_bulk(qty=400, loan_qty=0)
        order.action_confirm()
        self.assertEqual(len(order.pickup_picking_id.move_ids), 1)

    def test_return_picking_carries_loan_flag(self):
        if not self.env["stock.picking.type"].search([("code", "=", "incoming")], limit=1):
            self.skipTest("no incoming picking type available")
        order = self._make_bulk(qty=400, loan_qty=100)
        order.action_confirm()
        order.action_pickup()
        order.action_return()
        self.assertTrue(order.return_picking_id)
        return_moves = order.return_picking_id.move_ids
        self.assertEqual(len(return_moves), 2)
        self.assertEqual(sum(return_moves.filtered("is_loan").mapped("product_uom_qty")), 100.0)

    # ------------------------------------------------------------------
    # Loan return validation
    # ------------------------------------------------------------------
    def test_loan_return_validation_short_raises(self):
        if not self.env["stock.picking.type"].search([("code", "=", "incoming")], limit=1):
            self.skipTest("no incoming picking type available")
        order = self._make_bulk(qty=400, loan_qty=100)
        order.action_confirm()
        order.action_pickup()
        order.action_return()
        # Simulate operator received only 80 of 100 loan units
        loan_moves = order.return_picking_id.move_ids.filtered("is_loan")
        loan_moves.write({"quantity": 80.0})
        with self.assertRaises(UserError):
            order.action_validate_loan_return()

    def test_loan_return_validation_complete_passes(self):
        if not self.env["stock.picking.type"].search([("code", "=", "incoming")], limit=1):
            self.skipTest("no incoming picking type available")
        order = self._make_bulk(qty=400, loan_qty=100)
        order.action_confirm()
        order.action_pickup()
        order.action_return()
        order.return_picking_id.move_ids.filtered("is_loan").write({"quantity": 100.0})
        self.assertTrue(order.action_validate_loan_return())

    def test_loan_return_validation_noop_when_no_loan(self):
        order = self._make_bulk(qty=400, loan_qty=0)
        # No-op even without a return picking when loan_qty == 0
        self.assertTrue(order.action_validate_loan_return())

    # ------------------------------------------------------------------
    # BAST line propagation
    # ------------------------------------------------------------------
    def test_bast_pickup_has_loan_line(self):
        order = self._make_bulk(qty=400, loan_qty=100)
        order.action_confirm()
        order.action_generate_bast_pickup()
        self.assertTrue(order.bast_pickup_id)
        loan_lines = order.bast_pickup_id.line_ids.filtered("is_loan")
        self.assertEqual(len(loan_lines), 1)
        self.assertEqual(loan_lines.qty, 100.0)
