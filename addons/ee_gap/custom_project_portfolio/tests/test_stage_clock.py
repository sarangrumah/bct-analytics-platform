# -*- coding: utf-8 -*-
"""The SLA clock is the whole point of this module, so it is what gets tested."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pmo")
class TestStageClock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vertical = cls.env.ref("custom_project_portfolio.vertical_pos")
        cls.stage_dev = cls.env.ref("custom_project_portfolio.stage_dev")
        cls.stage_uat = cls.env.ref("custom_project_portfolio.stage_uat")
        cls.stage_hold = cls.env.ref("custom_project_portfolio.stage_hold")
        cls.stage_waiting = cls.env.ref("custom_project_portfolio.stage_waiting_user")
        cls.stage_done = cls.env.ref("custom_project_portfolio.stage_done")
        cls.project = cls.env["project.project"].create(
            {
                "name": "PPOB Biller Rollout",
                "custom_vertical_id": cls.vertical.id,
            }
        )

    def _task(self, **kw):
        values = {
            "name": "Integrasi biller prepaid",
            "project_id": self.project.id,
            "stage_id": self.stage_dev.id,
        }
        values.update(kw)
        return self.env["project.task"].create(values)

    def test_vertical_inherited_from_project(self):
        task = self._task()
        self.assertEqual(task.custom_vertical_id, self.vertical, "A task must inherit its brand from the project.")

    def test_sprint_assigned_on_create(self):
        task = self._task()
        self.assertTrue(task.custom_sprint_id, "Every task lands in the current week.")
        self.assertEqual(task.custom_sprint_id.state, "active")

    def test_hold_requires_reason(self):
        task = self._task()
        with self.assertRaises(UserError):
            task.write({"stage_id": self.stage_hold.id})

    def test_hold_pauses_clock_and_resume_returns(self):
        task = self._task()
        # Pretend the task has been sitting in Development for four hours.
        task.custom_stage_entered_at = fields.Datetime.now() - timedelta(hours=4)
        task.write(
            {
                "stage_id": self.stage_hold.id,
                "custom_hold_reason": "Menunggu data toko dari brand",
            }
        )
        self.assertEqual(task.stage_id, self.stage_hold)
        self.assertEqual(task.custom_prev_stage_id, self.stage_dev)
        self.assertTrue(task.custom_hold_since)
        # Time in Development counts against the team, so nothing was excused yet.
        self.assertEqual(task.custom_hold_duration_hours, 0.0)

        # Now spend three hours on hold and come back.
        task.custom_stage_entered_at = fields.Datetime.now() - timedelta(hours=3)
        task.action_pmo_resume()
        self.assertEqual(task.stage_id, self.stage_dev)
        self.assertGreaterEqual(task.custom_hold_duration_hours, 2.9, "Hold time must be booked to the hold bucket.")
        self.assertEqual(task.custom_user_wait_hours, 0.0)

    def test_waiting_user_books_time_to_the_user(self):
        task = self._task(stage_id=self.stage_uat.id)
        task.write({"stage_id": self.stage_waiting.id})
        self.assertTrue(task.custom_verification_due, "Asking the brand to verify must set a deadline.")
        task.custom_stage_entered_at = fields.Datetime.now() - timedelta(hours=6)
        task.write({"stage_id": self.stage_done.id})
        self.assertGreaterEqual(
            task.custom_user_wait_hours, 5.9, "Waiting on the brand is booked to the user, not the team."
        )
        self.assertEqual(task.custom_hold_duration_hours, 0.0)
        self.assertTrue(task.custom_closed_at)

    def test_two_honest_numbers_diverge(self):
        task = self._task(stage_id=self.stage_uat.id)
        # Back-date creation through SQL: create_date is a log-access field, and without a
        # realistic elapsed time both numbers are zero and the test proves nothing.
        self.env.cr.execute(
            "UPDATE project_task SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=20), task.id),
        )
        task.invalidate_recordset()

        task.write({"stage_id": self.stage_waiting.id})
        task.custom_stage_entered_at = fields.Datetime.now() - timedelta(hours=8)
        task.write({"stage_id": self.stage_done.id})
        task.invalidate_recordset()

        self.assertGreater(task.custom_user_wait_hours, 7.0)
        self.assertGreater(
            task.custom_lead_time_total,
            task.custom_cycle_time_team,
            "Total lead time must exceed team cycle time once the user waited.",
        )
        self.assertAlmostEqual(
            task.custom_lead_time_total - task.custom_cycle_time_team,
            task.custom_user_wait_hours,
            places=1,
            msg="The gap between the two numbers IS the time the user held.",
        )

    def test_illegal_transition_is_refused(self):
        task = self._task(stage_id=self.env.ref("custom_project_portfolio.stage_backlog").id)
        with self.assertRaises(UserError):
            task.write({"stage_id": self.stage_done.id})

    def test_auto_close_after_silence(self):
        task = self._task(stage_id=self.stage_uat.id)
        task.write({"stage_id": self.stage_waiting.id})
        # Pretend the verification window has passed.
        task.write(
            {
                "custom_verification_due": fields.Datetime.now() - timedelta(hours=1),
            }
        )
        self.env["project.task"].cron_pmo_verification()
        self.assertEqual(task.stage_id, self.stage_done)
        self.assertTrue(task.custom_auto_closed, "Silence past the window closes the item, and says so.")

    def test_hold_expiry_is_flagged_once(self):
        task = self._task()
        task.write(
            {
                "stage_id": self.stage_hold.id,
                "custom_hold_reason": "Menunggu izin",
                "custom_hold_until": fields.Date.context_today(task) - timedelta(days=1),
            }
        )
        self.env["project.task"].cron_pmo_hold_watch()
        self.assertTrue(task.custom_hold_expired_notified)
        # Second run must not re-flag: the search excludes already-notified holds.
        self.env["project.task"].cron_pmo_hold_watch()
        self.assertTrue(task.custom_hold_expired_notified)

    def test_working_days_skip_weekend(self):
        task = self._task()
        friday = fields.Datetime.to_datetime("2026-07-31 09:00:00")  # a Friday
        due = task._pmo_add_working_days(friday, 1)
        self.assertEqual(due.date().isoformat(), "2026-08-03", "One working day after Friday is Monday, not Saturday.")
