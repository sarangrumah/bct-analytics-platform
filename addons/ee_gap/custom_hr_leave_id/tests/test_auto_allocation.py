# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAutoAllocation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Employee = self.env["hr.employee"]
        self.LeaveType = self.env["hr.leave.type"]
        self.Allocation = self.env["hr.leave.allocation"]

        # Ensure at least one annual-leave type with the ID category exists.
        self.annual_type = self.LeaveType.search([("x_id_leave_category", "=", "cuti_tahunan")], limit=1)
        if not self.annual_type:
            self.annual_type = self.LeaveType.create(
                {
                    "name": "Cuti Tahunan (test)",
                    "x_id_leave_category": "cuti_tahunan",
                    "requires_allocation": True,
                    "allocation_validation_type": "hr",
                    "leave_validation_type": "hr",
                    "time_type": "leave",
                }
            )

    def test_auto_allocation_creates_record(self):
        before = self.Allocation.search_count([])
        employee = self.Employee.create({"name": "Test Auto Allocation", "x_auto_leave_allocation": True})
        after = self.Allocation.search_count([])
        # At least one new allocation should exist for this employee/type.
        self.assertGreater(after, before)
        alloc = self.Allocation.search(
            [
                ("employee_id", "=", employee.id),
                ("holiday_status_id", "=", self.annual_type.id),
            ],
            limit=1,
        )
        self.assertTrue(alloc, "Expected auto allocation for new employee")
        self.assertGreater(alloc.number_of_days, 0)
        self.assertLessEqual(alloc.number_of_days, 12)
        # date_to should be end of current year.
        today = fields.Date.context_today(employee)
        self.assertEqual(alloc.date_to, date(today.year, 12, 31))

    def test_no_allocation_when_flag_off(self):
        before = self.Allocation.search_count([])
        employee = self.Employee.create({"name": "Test No Auto", "x_auto_leave_allocation": False})
        after = self.Allocation.search_count([])
        self.assertEqual(after, before)
        self.assertFalse(self.Allocation.search([("employee_id", "=", employee.id)]))
