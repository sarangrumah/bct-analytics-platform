# -*- coding: utf-8 -*-
"""What makes a CR a CR: numbering, the analysis gate, tiered approval, response SLA."""

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pmo")
class TestChangeRequest(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vertical = cls.env.ref("custom_project_portfolio.vertical_retail")
        cls.project = cls.env["project.project"].create(
            {
                "name": "X70D rollout batch 2",
                "custom_vertical_id": cls.vertical.id,
            }
        )

    def _cr(self, **kw):
        values = {
            "name": "Kolom diskon bundling di laporan harian POS",
            "vertical_id": self.vertical.id,
            "project_id": self.project.id,
        }
        values.update(kw)
        return self.env["custom.change.request"].create(values)

    def test_official_number_is_assigned(self):
        cr = self._cr()
        self.assertTrue(cr.code.startswith("CR-"), "The brand quotes this number back at us.")
        self.assertIn(str(fields.Date.context_today(cr).year), cr.code)

    def test_lands_in_intake(self):
        cr = self._cr()
        self.assertEqual(cr.approval_state, "draft", "New requests wait in one triage queue.")

    def test_response_sla_runs_from_the_ask(self):
        cr = self._cr(priority="critical")
        self.assertTrue(cr.sla_response_due)
        self.assertGreater(cr.sla_response_due, cr.request_date)
        low = self._cr(priority="low")
        self.assertGreater(
            low.sla_response_due, cr.sla_response_due, "A low-priority ask gets a longer response window."
        )

    def test_triage_stamps_first_response(self):
        cr = self._cr()
        cr.action_start_analysis()
        self.assertEqual(cr.approval_state, "analysis")
        self.assertTrue(cr.first_response_at)
        self.assertTrue(cr.sla_response_met, "Triaged immediately, so the SLA was met.")

    def test_cannot_approve_without_impact_analysis(self):
        cr = self._cr()
        cr.action_start_analysis()
        with self.assertRaises(ValidationError):
            cr.action_submit_for_approval()

    def test_two_tiers_for_low_impact(self):
        cr = self._cr(impact="low")
        cr.action_start_analysis()
        cr.write(
            {
                "impact_analysis": "<p>Backward compatible column, feature-flagged.</p>",
                "effort_estimate_days": 4.5,
            }
        )
        cr.action_submit_for_approval()
        self.assertEqual(len(cr.approval_ids), 2)
        self.assertEqual(cr.approval_progress, "0/2")

    def test_three_tiers_for_high_impact(self):
        cr = self._cr(impact="critical")
        cr.action_start_analysis()
        cr.write({"impact_analysis": "<p>Touches money.</p>", "effort_estimate_days": 8.0})
        cr.action_submit_for_approval()
        self.assertEqual(len(cr.approval_ids), 3, "Critical impact pulls in the vertical owner.")

    def test_tiers_must_approve_in_order(self):
        cr = self._cr(impact="critical")
        cr.action_start_analysis()
        cr.write({"impact_analysis": "<p>x</p>", "effort_estimate_days": 2.0})
        cr.action_submit_for_approval()
        tier3 = cr.approval_ids.filtered(lambda a: a.tier == 3)
        with self.assertRaises(UserError):
            tier3.action_approve()

    def test_full_approval_flips_state(self):
        cr = self._cr(impact="low")
        cr.action_start_analysis()
        cr.write({"impact_analysis": "<p>x</p>", "effort_estimate_days": 1.0})
        cr.action_submit_for_approval()
        for line in cr.approval_ids.sorted("tier"):
            line.action_approve()
        self.assertEqual(cr.approval_state, "approved")

    def test_rejection_needs_a_reason(self):
        cr = self._cr()
        with self.assertRaises(UserError):
            cr.action_reject()
        cr.reject_reason = "Sudah tercakup CR-2026-0101"
        cr.action_reject()
        self.assertEqual(cr.approval_state, "rejected")

    def test_spawned_task_carries_origin(self):
        cr = self._cr(impact="low")
        cr.action_start_analysis()
        cr.write({"impact_analysis": "<p>x</p>", "effort_estimate_days": 1.0})
        cr.action_submit_for_approval()
        for line in cr.approval_ids.sorted("tier"):
            line.action_approve()
        cr.action_spawn_tasks()
        task = cr.task_ids
        self.assertEqual(len(task), 1)
        self.assertEqual(task.custom_source, "cr")
        self.assertEqual(task.custom_vertical_id, self.vertical, "A task inherits the brand from its request.")
        self.assertEqual(task.custom_cr_code, cr.code)

    def test_cannot_spawn_before_approval(self):
        cr = self._cr()
        with self.assertRaises(UserError):
            cr.action_spawn_tasks()
