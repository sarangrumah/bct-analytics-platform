# -*- coding: utf-8 -*-
from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_timesheet")
class TestOvertimeWorkEntry(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({"name": "OT Employee"})
        cls.project = cls.env["project.project"].create({"name": "OT Project"})

    def _validated_line(self, hours):
        line = self.env["account.analytic.line"].create(
            {
                "name": "Overtime work",
                "date": date.today(),
                "unit_amount": hours,
                "employee_id": self.employee.id,
                "project_id": self.project.id,
            }
        )
        line.action_submit_validation()
        if line.x_validation_state == "submitted":
            line.action_validate()
        return line

    def test_overtime_computed(self):
        line = self._validated_line(10.0)
        self.assertEqual(line.x_overtime_hours, 2.0)

    def test_action_create_overtime_work_entry(self):
        line = self._validated_line(11.0)
        self.assertEqual(line.x_overtime_hours, 3.0)
        work_entry = line.action_create_overtime_work_entry()
        self.assertTrue(work_entry, "Action must return a created work entry")
        self.assertEqual(work_entry.employee_id, self.employee)
        self.assertAlmostEqual(work_entry.duration, 3.0)
        self.assertEqual(work_entry.state, "draft")
        self.assertEqual(work_entry.work_entry_type_id.code, "OT")
        self.assertEqual(line.x_overtime_work_entry_id, work_entry)

    def test_overtime_zero_noop(self):
        line = self._validated_line(6.0)
        self.assertEqual(line.x_overtime_hours, 0.0)
        result = line.action_create_overtime_work_entry()
        self.assertFalse(result)

    def test_unlink_cancels_work_entry(self):
        line = self._validated_line(12.0)
        work_entry = line.action_create_overtime_work_entry()
        we_id = work_entry.id
        line.unlink()
        we = self.env["hr.work.entry"].browse(we_id)
        self.assertEqual(we.state, "cancelled")
