# -*- coding: utf-8 -*-
from datetime import datetime

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_maintenance")
class TestMtbfMttr(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Equipment = self.env["maintenance.equipment"]
        self.Request = self.env["maintenance.request"]
        self.Stage = self.env["maintenance.stage"]
        self.done_stage = self.Stage.search([("done", "=", True)], limit=1)
        if not self.done_stage:
            self.done_stage = self.Stage.create({"name": "Done (test)", "done": True, "sequence": 99})
        self.equipment = self.Equipment.create(
            {
                "name": "Test Equipment MTBF",
                "effective_date": datetime(2026, 1, 1).date(),
            }
        )

    def _make_done_request(self, request_dt, close_dt, kind="corrective"):
        req = self.Request.create(
            {
                "name": "Req %s" % request_dt,
                "equipment_id": self.equipment.id,
                "maintenance_type": kind,
                "request_date": request_dt,
            }
        )
        req.write({"close_date": close_dt, "stage_id": self.done_stage.id})
        return req

    def test_mtbf_with_three_failures(self):
        """MTBF = window_hours / failures."""
        self._make_done_request(datetime(2026, 1, 10, 8, 0), datetime(2026, 1, 10, 10, 0))
        self._make_done_request(datetime(2026, 2, 10, 8, 0), datetime(2026, 2, 10, 11, 0))
        self._make_done_request(datetime(2026, 3, 10, 8, 0), datetime(2026, 3, 10, 14, 0))
        # Recompute by invalidating
        self.equipment.invalidate_recordset()
        self.assertEqual(self.equipment.x_total_failures, 3)
        self.assertGreater(self.equipment.x_mtbf_hours, 0.0)
        # MTTR: avg of (2, 3, 6) = ~3.67h
        self.assertAlmostEqual(self.equipment.x_mttr_hours, (2.0 + 3.0 + 6.0) / 3.0, places=2)
        self.assertTrue(self.equipment.x_last_failure_at)

    def test_predicted_next_maintenance_from_mtbf(self):
        """Predicted date should be in the future when MTBF > 0."""
        self._make_done_request(datetime(2026, 1, 10, 8, 0), datetime(2026, 1, 10, 10, 0))
        self._make_done_request(datetime(2026, 2, 10, 8, 0), datetime(2026, 2, 10, 11, 0))
        self.equipment.invalidate_recordset()
        # MTBF should be set and predicted via MTBF
        self.assertEqual(self.equipment.x_predicted_via, "mtbf")
        self.assertTrue(self.equipment.x_predicted_next_maintenance)

    def test_no_failures_no_mtbf(self):
        """No corrective done requests -> mtbf = 0, predicted via manual."""
        self.equipment.invalidate_recordset()
        self.assertEqual(self.equipment.x_total_failures, 0)
        self.assertEqual(self.equipment.x_mtbf_hours, 0.0)
        self.assertEqual(self.equipment.x_mttr_hours, 0.0)
        self.assertIn(self.equipment.x_predicted_via, ("manual", "iot"))

    def test_schedule_predicted_maintenance_creates_preventive(self):
        """Button creates a preventive draft request."""
        self._make_done_request(datetime(2026, 1, 10, 8, 0), datetime(2026, 1, 10, 10, 0))
        self._make_done_request(datetime(2026, 2, 10, 8, 0), datetime(2026, 2, 10, 11, 0))
        self.equipment.invalidate_recordset()
        action = self.equipment.action_schedule_predicted_maintenance()
        self.assertTrue(action)
        new_req = self.Request.search(
            [
                ("equipment_id", "=", self.equipment.id),
                ("maintenance_type", "=", "preventive"),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(new_req)
        self.assertIn("Predicted Maintenance", new_req.name)
