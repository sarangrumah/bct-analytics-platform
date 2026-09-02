# -*- coding: utf-8 -*-
"""The claim being tested: an event raised from ANY origin lands in the outbox.

That is the whole reason the event is born in Odoo instead of in the BFF, so it is the
thing that has to be proven -- including for a stage change made straight on the ORM,
which is what a Jira webhook or the Odoo backend would do.
"""

import json

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pmo")
class TestNotifyOutbox(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.outbox = cls.env["custom.project.notify.outbox"]
        cls.log = cls.env["custom.project.notify.log"]
        cls.vertical = cls.env.ref("custom_project_portfolio.vertical_pos")
        cls.brand_pic = cls.env["res.partner"].create(
            {
                "name": "Sari W.",
                "phone": "081234567890",
                "email": "sari@example.test",
            }
        )
        cls.vertical.write(
            {
                "pic_partner_ids": [(6, 0, cls.brand_pic.ids)],
                "vertical_po_id": cls.env.uid,
            }
        )
        cls.assignee = cls.env["res.users"].create(
            {
                "name": "Dimas P.",
                "login": "dimas.pmo.test",
                "email": "dimas@example.test",
            }
        )
        cls.assignee.partner_id.phone = "081298765432"
        cls.project = cls.env["project.project"].create(
            {
                "name": "PPOB Biller Rollout",
                "custom_vertical_id": cls.vertical.id,
                "custom_po_id": cls.env.uid,
                "custom_ba_id": cls.env.uid,
            }
        )

    def _task(self, **kw):
        values = {
            "name": "Integrasi biller prepaid",
            "project_id": self.project.id,
            "stage_id": self.env.ref("custom_project_portfolio.stage_dev").id,
            "user_ids": [(6, 0, self.assignee.ids)],
        }
        values.update(kw)
        return self.env["project.task"].create(values)

    def test_task_creation_queues_a_row(self):
        before = self.outbox.search_count([])
        task = self._task()
        rows = self.outbox.search(
            [
                ("res_model", "=", "project.task"),
                ("res_id", "=", task.id),
                ("event", "=", "task_created"),
            ]
        )
        self.assertTrue(rows, "Creating a task must queue a notification.")
        self.assertGreater(self.outbox.search_count([]), before)
        self.assertEqual(rows[0].state, "pending")

    def test_payload_carries_vertical_and_url(self):
        task = self._task()
        row = self.outbox.search(
            [
                ("res_id", "=", task.id),
                ("event", "=", "task_created"),
            ],
            limit=1,
        )
        payload = json.loads(row.payload_json)
        self.assertEqual(payload["vertical"]["code"], "POS")
        self.assertIn(
            "Example Group",
            payload["vertical"]["label"],
            "The WA template needs the legal entity when we know it.",
        )
        self.assertIn(f"/tasks/{task.id}", payload["url"])
        self.assertTrue(payload["recipients"], "A queued row must name its recipients.")

    def test_orm_stage_change_is_not_silent(self):
        """A Jira webhook or the Odoo backend writes straight on the ORM. It must notify."""
        task = self._task()
        task.write({"stage_id": self.env.ref("custom_project_portfolio.stage_uat").id})
        rows = self.outbox.search(
            [
                ("res_id", "=", task.id),
                ("event", "=", "stage_changed"),
            ]
        )
        self.assertTrue(rows, "An ORM-level stage change must still raise the event.")

    def test_verification_request_targets_the_brand(self):
        task = self._task(stage_id=self.env.ref("custom_project_portfolio.stage_uat").id)
        task.write(
            {
                "stage_id": self.env.ref("custom_project_portfolio.stage_waiting_user").id,
            }
        )
        row = self.outbox.search(
            [
                ("res_id", "=", task.id),
                ("event", "=", "verify_request"),
            ],
            limit=1,
        )
        self.assertTrue(row)
        payload = json.loads(row.payload_json)
        kinds = {r["kind"] for r in payload["recipients"]}
        self.assertIn("brand_pic", kinds, "Verification is asked of the brand, not the team.")
        self.assertTrue(payload["context"]["verification_due"])

    def test_hold_event_reaches_the_po(self):
        task = self._task()
        task.write(
            {
                "stage_id": self.env.ref("custom_project_portfolio.stage_hold").id,
                "custom_hold_reason": "Menunggu data toko dari brand",
            }
        )
        row = self.outbox.search(
            [
                ("res_id", "=", task.id),
                ("event", "=", "on_hold"),
            ],
            limit=1,
        )
        self.assertTrue(row)
        payload = json.loads(row.payload_json)
        self.assertIn("Menunggu data toko", payload["context"]["hold_reason"])

    def test_no_recipient_is_recorded_as_a_finding(self):
        """ "Nobody was reachable" must be visible, not swallowed.

        ``user_ids`` is cleared explicitly: Odoo defaults a new task's assignee to whoever
        created it, which would quietly give this task a reachable recipient and make the
        test pass for the wrong reason.
        """
        empty_project = self.env["project.project"].create({"name": "Orphan project"})
        task = self.env["project.task"].create(
            {
                "name": "Nobody owns this",
                "project_id": empty_project.id,
                "stage_id": self.env.ref("custom_project_portfolio.stage_backlog").id,
                "user_ids": [(5, 0, 0)],
            }
        )
        self.assertFalse(task.user_ids, "Test precondition: the task really has no assignee.")
        skipped = self.log.search(
            [
                ("res_id", "=", task.id),
                ("skipped_reason", "!=", False),
            ]
        )
        self.assertTrue(skipped, "A rule that resolved to nobody should leave a trace.")

    def test_dispatch_without_config_keeps_row_pending(self):
        """No BFF deployed yet must not burn the retry budget."""
        self.env["ir.config_parameter"].sudo().set_param("custom_project_notify.bff_url", "")
        task = self._task()
        row = self.outbox.search([("res_id", "=", task.id)], limit=1)
        self.outbox.cron_dispatch()
        row.invalidate_recordset()
        self.assertEqual(row.state, "pending")
        self.assertEqual(row.attempt, 0, "An unconfigured BFF is not a failed attempt.")

    def test_retry_backoff_then_give_up(self):
        task = self._task()
        row = self.outbox.search([("res_id", "=", task.id)], limit=1)
        for _ in range(4):
            row._mark_failed("boom")
        self.assertEqual(row.state, "pending")
        self.assertTrue(row.next_retry_at)
        row._mark_failed("boom")
        self.assertEqual(row.state, "failed", "Five attempts is where we stop and shout.")
        row.action_retry()
        self.assertEqual(row.state, "pending")

    def test_phone_masking(self):
        self.assertEqual(self.log.mask_phone("081234567890"), "081•••••7890")
        self.assertEqual(self.log.mask_phone(""), "")

    def test_rules_are_data_not_code(self):
        rule = self.env.ref("custom_project_notify.rule_task_created_assignee")
        rule.channel_wa = False
        task = self._task()
        row = self.outbox.search(
            [
                ("res_id", "=", task.id),
                ("event", "=", "task_created"),
            ],
            limit=1,
        )
        payload = json.loads(row.payload_json)
        assignee = [r for r in payload["recipients"] if r["kind"] == "assignee"]
        self.assertTrue(assignee)
        self.assertFalse(assignee[0].get("wa"), "Turning a channel off in the rule table must take effect at once.")

    def test_sla_cron_only_fires_while_clock_runs(self):
        overdue = fields.Datetime.subtract(fields.Datetime.now(), days=5)
        running = self._task(custom_due_sla_date=overdue)
        held = self._task(custom_due_sla_date=overdue)
        held.write(
            {
                "stage_id": self.env.ref("custom_project_portfolio.stage_hold").id,
                "custom_hold_reason": "Menunggu vendor",
            }
        )
        self.env["project.task"].cron_pmo_sla()
        self.assertTrue(
            self.outbox.search_count(
                [
                    ("res_id", "=", running.id),
                    ("event", "in", ["overdue", "escalation"]),
                ]
            ),
            "Work whose clock is running and is late must escalate.",
        )
        self.assertFalse(
            self.outbox.search_count(
                [
                    ("res_id", "=", held.id),
                    ("event", "in", ["overdue", "escalation"]),
                ]
            ),
            "Work on hold must not be chased — its clock is paused.",
        )
