# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_attendance")
class TestOvertimeWorkEntry(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Employee = self.env["hr.employee"]
        self.Attendance = self.env["hr.attendance"]
        self.employee = self.Employee.create({"name": "OT Tester"})

    def test_overtime_work_entry_creation(self):
        """A 10h attendance (8h threshold) produces a 2h hr.work.entry."""
        check_in = datetime(2026, 5, 18, 8, 0, 0)  # Monday
        check_out = check_in + timedelta(hours=10)
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": check_in,
                "check_out": check_out,
            }
        )
        # Worked hours should be 10 -> overtime 2
        self.assertAlmostEqual(att.x_overtime_hours, 2.0, places=2)
        work_entry = att.action_create_overtime_work_entry()
        self.assertTrue(work_entry)
        self.assertEqual(att.x_payroll_work_entry_id, work_entry)
        self.assertTrue(att.x_payroll_synced)
        self.assertEqual(work_entry.employee_id, self.employee)
        self.assertAlmostEqual(work_entry.duration, 2.0, places=2)
        wet = work_entry.work_entry_type_id
        self.assertEqual(wet.code, "OT")

    def test_overtime_zero_no_entry(self):
        """A short shift below threshold should not create a work entry."""
        check_in = datetime(2026, 5, 18, 9, 0, 0)
        check_out = check_in + timedelta(hours=6)
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": check_in,
                "check_out": check_out,
            }
        )
        self.assertEqual(att.x_overtime_hours, 0.0)
        result = att.action_create_overtime_work_entry()
        self.assertFalse(result)
        self.assertFalse(att.x_payroll_work_entry_id)
