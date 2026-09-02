# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_maintenance")
class TestMaintenanceSla(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Sla = self.env["custom.maintenance.team.sla"]
        self.Team = self.env["maintenance.team"]
        self.Equipment = self.env["maintenance.equipment"]
        self.Request = self.env["maintenance.request"]
        self.Stage = self.env["maintenance.stage"]
        self.team = self.Team.create({"name": "SLA Test Team"})
        self.equipment = self.Equipment.create({"name": "SLA Test Equipment", "maintenance_team_id": self.team.id})
        self.sla = self.Sla.create(
            {
                "name": "High SLA",
                "team_id": self.team.id,
                "priority": "3",
                "response_hours": 1,
                "resolve_hours": 4,
            }
        )

    def test_sla_assigned_on_create(self):
        """Request with matching priority/team gets SLA deadlines."""
        req = self.Request.create(
            {
                "name": "SLA req",
                "equipment_id": self.equipment.id,
                "maintenance_team_id": self.team.id,
                "priority": "3",
            }
        )
        self.assertEqual(req.x_sla_id, self.sla)
        self.assertTrue(req.x_sla_response_deadline)
        self.assertTrue(req.x_sla_resolve_deadline)
        # Resolve deadline should be ~4h after create
        delta_h = (req.x_sla_resolve_deadline - req.create_date).total_seconds() / 3600.0
        self.assertAlmostEqual(delta_h, 4.0, places=1)

    def test_sla_status_breach_and_cron(self):
        """A request past its deadline transitions to 'breach' and cron notifies."""
        req = self.Request.create(
            {
                "name": "SLA breach req",
                "equipment_id": self.equipment.id,
                "maintenance_team_id": self.team.id,
                "priority": "3",
            }
        )
        # Force deadline into the past
        req.write({"x_sla_resolve_deadline": datetime.now() - timedelta(hours=1)})
        req._compute_sla_status()
        self.assertEqual(req.x_sla_status, "breach")
        self.assertFalse(req.x_sla_breach_notified)
        self.Request.cron_check_sla_breach()
        req.invalidate_recordset()
        self.assertTrue(req.x_sla_breach_notified)

    def test_sla_done_status(self):
        """Closed requests show 'done' status irrespective of deadline."""
        req = self.Request.create(
            {
                "name": "SLA done req",
                "equipment_id": self.equipment.id,
                "maintenance_team_id": self.team.id,
                "priority": "3",
            }
        )
        done_stage = self.Stage.search([("done", "=", True)], limit=1)
        if not done_stage:
            done_stage = self.Stage.create({"name": "Done (test)", "done": True, "sequence": 99})
        req.write({"stage_id": done_stage.id})
        req._compute_sla_status()
        self.assertEqual(req.x_sla_status, "done")

    def test_sla_priority_constraint(self):
        """Unique constraint on team+priority is enforced."""
        with self.assertRaises(Exception):
            self.Sla.create(
                {
                    "name": "Duplicate",
                    "team_id": self.team.id,
                    "priority": "3",
                    "response_hours": 2,
                    "resolve_hours": 8,
                }
            )

    def test_cost_compute(self):
        """Total cost = labor + parts."""
        product = self.env["product.product"].create(
            {
                "name": "Bolt",
                "type": "consu",
                "list_price": 12.5,
            }
        )
        req = self.Request.create(
            {
                "name": "Cost req",
                "equipment_id": self.equipment.id,
                "priority": "2",
                "x_labor_cost": 100.0,
                "x_spare_part_ids": [(6, 0, [product.id])],
            }
        )
        req._compute_parts_cost()
        req._compute_total_cost()
        self.assertAlmostEqual(req.x_parts_cost, 12.5, places=2)
        self.assertAlmostEqual(req.x_total_cost, 112.5, places=2)
