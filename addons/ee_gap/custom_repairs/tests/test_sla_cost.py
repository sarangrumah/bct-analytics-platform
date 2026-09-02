# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_repairs")
class TestRepairSlaCost(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Repair = self.env["repair.order"]

    def test_sla_status_breached_when_past_promised(self):
        yesterday = fields.Date.context_today(self.Repair) - timedelta(days=2)
        repair = self.Repair.create({"x_promised_completion_date": yesterday})
        self.assertEqual(repair.x_sla_status, "breached")

    def test_sla_status_on_track_when_future(self):
        future = fields.Date.context_today(self.Repair) + timedelta(days=10)
        repair = self.Repair.create({"x_promised_completion_date": future})
        self.assertEqual(repair.x_sla_status, "on_track")

    def test_labor_cost_computes(self):
        repair = self.Repair.create({"x_labor_hours": 2.0, "x_labor_rate": 50000.0})
        self.assertEqual(repair.x_labor_cost, 100000.0)
        self.assertEqual(
            repair.x_total_repair_cost,
            repair.x_labor_cost + repair.x_material_cost,
        )
