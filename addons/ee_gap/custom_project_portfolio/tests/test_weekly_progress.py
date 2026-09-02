# -*- coding: utf-8 -*-
"""The weekly report must write its own factual half, and refuse to be submitted empty."""

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pmo")
class TestWeeklyProgress(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vertical = cls.env.ref("custom_project_portfolio.vertical_retail")
        cls.stage_uat = cls.env.ref("custom_project_portfolio.stage_uat")
        cls.stage_done = cls.env.ref("custom_project_portfolio.stage_done")
        cls.stage_hold = cls.env.ref("custom_project_portfolio.stage_hold")
        cls.project = cls.env["project.project"].create(
            {
                "name": "X70D rollout batch 2",
                "custom_vertical_id": cls.vertical.id,
                "custom_ba_id": cls.env.user.id,
            }
        )
        cls.sprint = cls.env["custom.project.sprint"].current_sprint()

    def test_sprint_is_one_iso_week(self):
        self.assertRegex(self.sprint.week_code, r"^\d{4}-W\d{2}$")
        self.assertEqual(self.sprint.date_start.weekday(), 0, "Sprints start on Monday.")
        self.assertEqual(self.sprint.date_end.weekday(), 4, "Sprints end on Friday.")

    def test_draft_cron_fills_the_factual_half(self):
        done = self.env["project.task"].create(
            {
                "name": "Migrasi master toko",
                "project_id": self.project.id,
                "stage_id": self.stage_uat.id,
                "custom_story_points": 5,
            }
        )
        done.write({"stage_id": self.env.ref("custom_project_portfolio.stage_waiting_user").id})
        done.write({"stage_id": self.stage_done.id})
        self.env["project.task"].create(
            {
                "name": "UAT struk",
                "project_id": self.project.id,
                "stage_id": self.stage_uat.id,
                "custom_story_points": 3,
            }
        )

        self.env["custom.weekly.progress"].cron_draft_weekly()
        report = self.env["custom.weekly.progress"].search(
            [
                ("sprint_id", "=", self.sprint.id),
                ("project_id", "=", self.project.id),
            ],
            limit=1,
        )
        self.assertTrue(report, "The cron drafts one report per active project.")
        self.assertEqual(report.state, "draft")
        self.assertEqual(report.done_count, 1)
        self.assertEqual(report.done_points, 5)
        self.assertEqual(report.carry_over_count, 1, "Unfinished work is carry-over.")
        self.assertEqual(report.author_id, self.env.user, "Drafts land on the project's BA.")

    def test_draft_cron_is_idempotent(self):
        model = self.env["custom.weekly.progress"]
        model.cron_draft_weekly()
        first = model.search_count([("sprint_id", "=", self.sprint.id)])
        model.cron_draft_weekly()
        second = model.search_count([("sprint_id", "=", self.sprint.id)])
        self.assertEqual(first, second, "Running twice must not duplicate reports.")

    def test_blocker_seeded_from_hold(self):
        task = self.env["project.task"].create(
            {
                "name": "Migrasi master toko batch 2",
                "project_id": self.project.id,
                "stage_id": self.env.ref("custom_project_portfolio.stage_dev").id,
            }
        )
        task.write(
            {
                "stage_id": self.stage_hold.id,
                "custom_hold_reason": "Menunggu data toko dari brand",
            }
        )
        report = self.env["custom.weekly.progress"].create(
            {
                "sprint_id": self.sprint.id,
                "project_id": self.project.id,
                "vertical_id": self.vertical.id,
            }
        )
        report._fill_automatic()
        self.assertIn(
            "Menunggu data toko", report.blocker or "", "A hold with a reason should pre-fill the blocker narrative."
        )
        self.assertEqual(report.hold_count, 1)

    def test_submit_requires_next_week(self):
        report = self.env["custom.weekly.progress"].create(
            {
                "sprint_id": self.sprint.id,
                "project_id": self.project.id,
            }
        )
        with self.assertRaises(UserError):
            report.action_submit()
        report.next_week = "Go-live terbatas 3 agen"
        report.action_submit()
        self.assertEqual(report.state, "submitted")
        self.assertTrue(report.submitted_at)

    def test_sprint_roll_carries_unfinished_work(self):
        task = self.env["project.task"].create(
            {
                "name": "Sisa pekerjaan",
                "project_id": self.project.id,
                "stage_id": self.stage_uat.id,
            }
        )
        # Force the sprint to look finished.
        self.sprint.write(
            {
                "date_end": fields.Date.subtract(fields.Date.context_today(self.sprint), days=1),
                "state": "active",
            }
        )
        self.env["custom.project.sprint"].cron_roll_sprint()
        self.assertEqual(self.sprint.state, "closed")
        task.invalidate_recordset()
        self.assertNotEqual(task.custom_sprint_id, self.sprint, "Unfinished work moves to the next week.")
        self.assertTrue(task.custom_carried_over)
