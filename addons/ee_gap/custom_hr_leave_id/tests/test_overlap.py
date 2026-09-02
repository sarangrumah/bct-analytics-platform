# -*- coding: utf-8 -*-
from datetime import date, datetime

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOverlappingHolidays(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Holiday = self.env["id.public.holiday"]
        self.LeaveType = self.env["hr.leave.type"]
        self.Leave = self.env["hr.leave"]
        self.Employee = self.env["hr.employee"]

        # Seed a couple of holidays in a controlled window.
        self.h1 = self.Holiday.create(
            {
                "name": "Test Holiday A",
                "date": date(2025, 6, 10),
                "type_code": "national",
            }
        )
        self.h2 = self.Holiday.create(
            {
                "name": "Test Holiday B",
                "date": date(2025, 6, 12),
                "type_code": "national",
            }
        )

        self.employee = self.Employee.create({"name": "Test Overlap Employee", "x_auto_leave_allocation": False})
        self.leave_type = self.LeaveType.create(
            {
                "name": "Test Type Overlap",
                "requires_allocation": False,
                "leave_validation_type": "no_validation",
                "time_type": "leave",
            }
        )

    def _make_leave(self, d_from, d_to):
        return self.Leave.new(
            {
                "employee_id": self.employee.id,
                "holiday_status_id": self.leave_type.id,
                "request_date_from": d_from,
                "request_date_to": d_to,
                "date_from": datetime.combine(d_from, datetime.min.time()),
                "date_to": datetime.combine(d_to, datetime.max.time()),
            }
        )

    def test_overlap_two_holidays(self):
        leave = self._make_leave(date(2025, 6, 9), date(2025, 6, 13))
        leave._compute_x_overlapping_holidays()
        self.assertEqual(leave.x_overlapping_holidays_count, 2)
        self.assertIn(self.h1, leave.x_overlapping_holidays)
        self.assertIn(self.h2, leave.x_overlapping_holidays)
        self.assertTrue(leave.x_overlapping_holidays_warning)

    def test_no_overlap(self):
        leave = self._make_leave(date(2025, 7, 1), date(2025, 7, 5))
        leave._compute_x_overlapping_holidays()
        self.assertEqual(leave.x_overlapping_holidays_count, 0)
        self.assertFalse(leave.x_overlapping_holidays_warning)
