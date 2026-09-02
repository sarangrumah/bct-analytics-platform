# -*- coding: utf-8 -*-
"""BBM log: total, consumption km/L, odometer sync."""

from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBbmConsumption(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Vehicle = self.env["fleet.vehicle"]
        self.Log = self.env["custom.fleet.bbm.log"]
        Model = self.env["fleet.vehicle.model"]
        Brand = self.env["fleet.vehicle.model.brand"]
        brand = Brand.search([], limit=1) or Brand.create({"name": "TestBrand"})
        model = Model.search([("brand_id", "=", brand.id)], limit=1) or Model.create(
            {"name": "TestModel", "brand_id": brand.id}
        )
        self.vehicle = self.Vehicle.create(
            {
                "model_id": model.id,
                "license_plate": "B 1234 ABC",
            }
        )

    def test_total_compute(self):
        log = self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": date.today(),
                "odometer_km": 1000,
                "liter": 10.0,
                "price_per_liter": 12500.0,
            }
        )
        self.assertAlmostEqual(log.total, 125000.0, places=2)

    def test_consumption_no_previous_log_is_zero(self):
        log = self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": date.today(),
                "odometer_km": 1000,
                "liter": 10.0,
                "price_per_liter": 12500.0,
            }
        )
        self.assertEqual(log.consumption_km_per_l, 0.0)

    def test_consumption_computes_from_previous_log(self):
        d0 = date.today() - timedelta(days=7)
        d1 = date.today()
        # First refuel at 1000 km
        self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": d0,
                "odometer_km": 1000,
                "liter": 10.0,
                "price_per_liter": 10000.0,
            }
        )
        # Second refuel at 1500 km using 50 L -> 500 km / 50 L = 10 km/L
        log2 = self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": d1,
                "odometer_km": 1500,
                "liter": 50.0,
                "price_per_liter": 10000.0,
            }
        )
        self.assertAlmostEqual(log2.consumption_km_per_l, 10.0, places=2)

    def test_consumption_ignores_other_vehicles(self):
        # Other vehicle's log at higher odometer must not affect consumption
        Model = self.env["fleet.vehicle.model"]
        Brand = self.env["fleet.vehicle.model.brand"]
        brand = Brand.search([], limit=1)
        model = Model.search([("brand_id", "=", brand.id)], limit=1)
        other = self.Vehicle.create(
            {
                "model_id": model.id,
                "license_plate": "D 9999 ZZZ",
            }
        )
        self.Log.create(
            {
                "vehicle_id": other.id,
                "date": date.today(),
                "odometer_km": 500,
                "liter": 5.0,
                "price_per_liter": 10000.0,
            }
        )
        log = self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": date.today(),
                "odometer_km": 800,
                "liter": 8.0,
                "price_per_liter": 10000.0,
            }
        )
        # No previous log for self.vehicle -> 0
        self.assertEqual(log.consumption_km_per_l, 0.0)

    def test_negative_liter_raises(self):
        with self.assertRaises(ValidationError):
            self.Log.create(
                {
                    "vehicle_id": self.vehicle.id,
                    "date": date.today(),
                    "odometer_km": 100,
                    "liter": -1.0,
                    "price_per_liter": 10000.0,
                }
            )

    def test_odometer_pushed_to_vehicle(self):
        self.Log.create(
            {
                "vehicle_id": self.vehicle.id,
                "date": date.today(),
                "odometer_km": 5000,
                "liter": 10.0,
                "price_per_liter": 10000.0,
            }
        )
        self.assertEqual(self.vehicle.x_current_odometer, 5000)

    def test_driver_assignment_history_created_and_transferred(self):
        Partner = self.env["res.partner"]
        d1 = Partner.create({"name": "Driver One"})
        d2 = Partner.create({"name": "Driver Two"})
        # Assign first driver
        self.vehicle.write({"x_driver_partner_id": d1.id})
        Assignment = self.env["custom.fleet.driver.assignment"]
        active = Assignment.search(
            [
                ("vehicle_id", "=", self.vehicle.id),
                ("status", "=", "active"),
            ]
        )
        self.assertEqual(len(active), 1)
        self.assertEqual(active.partner_id, d1)
        # Transfer to second driver
        self.vehicle.write({"x_driver_partner_id": d2.id})
        history = Assignment.search([("vehicle_id", "=", self.vehicle.id)])
        self.assertEqual(len(history), 2)
        ended = history.filtered(lambda a: a.status == "transferred")
        active = history.filtered(lambda a: a.status == "active")
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended.partner_id, d1)
        self.assertEqual(len(active), 1)
        self.assertEqual(active.partner_id, d2)
        # Clear driver
        self.vehicle.write({"x_driver_partner_id": False})
        active = Assignment.search(
            [
                ("vehicle_id", "=", self.vehicle.id),
                ("status", "=", "active"),
            ]
        )
        self.assertFalse(active)

    def test_service_due_compute(self):
        self.vehicle.write(
            {
                "x_current_odometer": 10000,
                "x_next_service_km": 10500,
                "x_next_service_date": False,
            }
        )
        self.assertFalse(self.vehicle.x_service_due)
        self.vehicle.write({"x_current_odometer": 10600})
        self.assertTrue(self.vehicle.x_service_due)

    def test_service_due_by_date(self):
        self.vehicle.write(
            {
                "x_current_odometer": 0,
                "x_next_service_km": 0,
                "x_next_service_date": date.today() - timedelta(days=1),
            }
        )
        self.assertTrue(self.vehicle.x_service_due)
