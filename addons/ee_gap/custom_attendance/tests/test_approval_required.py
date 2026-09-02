# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_attendance")
class TestApprovalRequiredAnomaly(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Employee = self.env["hr.employee"]
        self.Attendance = self.env["hr.attendance"]
        self.employee = self.Employee.create({"name": "Approval Tester"})

    def test_long_shift_requires_approval(self):
        """worked_hours > 12 -> approval required."""
        check_in = datetime(2026, 5, 18, 8, 0, 0)
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": check_in,
                "check_out": check_in + timedelta(hours=13),
            }
        )
        self.assertTrue(att.x_approval_required)

    def test_late_night_checkin_requires_approval(self):
        """check_in after 22:00 -> approval required."""
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 5, 18, 23, 30, 0),
                "check_out": datetime(2026, 5, 19, 1, 0, 0),
            }
        )
        self.assertTrue(att.x_approval_required)

    def test_early_morning_checkin_requires_approval(self):
        """check_in before 05:00 -> approval required."""
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 5, 18, 3, 30, 0),
                "check_out": datetime(2026, 5, 18, 7, 0, 0),
            }
        )
        self.assertTrue(att.x_approval_required)

    def test_normal_shift_no_approval(self):
        """8h shift starting 09:00 -> no approval needed."""
        check_in = datetime(2026, 5, 18, 9, 0, 0)
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": check_in,
                "check_out": check_in + timedelta(hours=8),
            }
        )
        self.assertFalse(att.x_approval_required)

    def test_approval_workflow_transitions(self):
        """draft -> pending -> approved with audit fields populated."""
        att = self.Attendance.create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 5, 18, 8, 0, 0),
                "check_out": datetime(2026, 5, 18, 21, 0, 0),
            }
        )
        self.assertEqual(att.x_approval_state, "draft")
        att.action_request_approval()
        self.assertEqual(att.x_approval_state, "pending")
        att.action_approve()
        self.assertEqual(att.x_approval_state, "approved")
        self.assertEqual(att.x_approval_by, self.env.user)
