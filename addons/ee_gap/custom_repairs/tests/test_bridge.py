# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_repairs")
class TestRepairMaintenanceBridge(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Repair = self.env["repair.order"]
        self.Equipment = self.env["maintenance.equipment"]
        self.Request = self.env["maintenance.request"]
        self.equipment = self.Equipment.create({"name": "Bridge Test Asset"})

    def test_bridge_creates_request_on_confirm(self):
        """Confirming a repair with an asset opens a maintenance.request."""
        repair = self.Repair.create(
            {
                "x_equipment_id": self.equipment.id,
                "x_id_complaint": "Motor overheating",
            }
        )
        self.assertFalse(repair.x_maintenance_request_id)
        repair.write({"state": "confirmed"})
        request = repair.x_maintenance_request_id
        self.assertTrue(request, "maintenance.request should be created")
        self.assertEqual(request.equipment_id, self.equipment)
        self.assertEqual(request.maintenance_type, "corrective")
        self.assertIn(request, self.equipment.maintenance_ids)

    def test_bridge_is_idempotent(self):
        """Re-confirming does not create a second request."""
        repair = self.Repair.create({"x_equipment_id": self.equipment.id})
        repair.write({"state": "confirmed"})
        first = repair.x_maintenance_request_id
        self.assertTrue(first)
        # Simulate a re-confirm (e.g. after cancel).
        repair.write({"state": "confirmed"})
        self.assertEqual(repair.x_maintenance_request_id, first)

    def test_bridge_noop_without_equipment(self):
        """No asset linked -> no maintenance.request."""
        repair = self.Repair.create({})
        repair.write({"state": "confirmed"})
        self.assertFalse(repair.x_maintenance_request_id)
