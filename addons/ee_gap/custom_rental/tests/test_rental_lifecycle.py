# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestRentalLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Test Renter"})
        cls.asset = cls.env["rental.asset"].create(
            {
                "name": "Asset One",
                "code": "ASSET-LC-1",
                "daily_rate": 50.0,
            }
        )

    def _make_order(self, days=2):
        now = datetime(2025, 1, 1, 10, 0)
        return self.env["rental.order"].create(
            {
                "partner_id": self.partner.id,
                "asset_id": self.asset.id,
                "pickup_dt": now,
                "return_dt_expected": now + timedelta(days=days),
                "daily_rate": 50.0,
            }
        )

    def test_schedule_overlap_rejected(self):
        self._make_order(days=3).action_confirm()
        with self.assertRaises(ValidationError):
            self._make_order(days=2).action_confirm()

    def test_late_fee_cron(self):
        order = self._make_order(days=1)
        order.action_confirm()
        order.action_pickup()
        # Backdate so return is overdue
        order.return_dt_expected = fields.Datetime.now() - timedelta(days=2)
        order.late_fee_rate = 10.0
        before = order.late_fee_total
        self.env["rental.order"]._cron_accrue_late_fees()
        order.invalidate_recordset(["late_fee_total"])
        self.assertGreater(order.late_fee_total, before)
        self.assertTrue(order.late_fee_line_ids)

    def test_picking_creation_when_enabled(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_rental.config_stock_integration", "True")
        product = self.env["product.product"].create(
            {
                "name": "Asset Stock",
                "type": "consu",
                "is_storable": True,
            }
        )
        self.asset.product_id = product.id
        order = self._make_order(days=1)
        order.action_confirm()
        # picking should have been created when picking types exist
        if self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1):
            self.assertTrue(order.pickup_picking_id)
