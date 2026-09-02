# -*- coding: utf-8 -*-
"""SLA escalation: auto_approve / escalate_to_next / escalate_to_user."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields

from .common import ApprovalTestCommon


class TestEscalation(ApprovalTestCommon):
    def _expire(self, req):
        """Force the request's due_at to the past so it counts as overdue."""
        req.write({"due_at": fields.Datetime.now() - timedelta(hours=1)})

    def test_auto_approve_advances(self):
        m = self._make_matrix("Auto-approve chain")
        self._add_tier(m, sequence=10, name="T1", approvers=[self.user_approver_a], on_overdue="auto_approve")
        self._add_tier(m, sequence=20, name="T2", approvers=[self.user_approver_b])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self._expire(req)
        self.Request._cron_check_escalations()
        req.invalidate_recordset()

        self.assertEqual(req.current_tier_id.name, "T2")
        # An auto-approved line should exist for T1
        auto = req.history_ids.filtered(lambda l: l.tier_name == "T1" and l.action == "approved")
        self.assertTrue(auto)

    def test_escalate_to_next_advances(self):
        m = self._make_matrix("Escalate chain")
        self._add_tier(m, sequence=10, name="T1", approvers=[self.user_approver_a], on_overdue="escalate_to_next")
        self._add_tier(m, sequence=20, name="T2", approvers=[self.user_approver_b])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self._expire(req)
        self.Request._cron_check_escalations()
        req.invalidate_recordset()

        self.assertEqual(req.current_tier_id.name, "T2")
        escalated = req.history_ids.filtered(lambda l: l.tier_name == "T1" and l.action == "escalated")
        self.assertTrue(escalated)

    def test_escalate_to_user_reroutes_approver(self):
        m = self._make_matrix("Fallback chain")
        self._add_tier(
            m,
            sequence=10,
            name="T1",
            approvers=[self.user_approver_a],
            on_overdue="escalate_to_user",
            escalation_user=self.user_fallback,
        )
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()
        self.assertIn(self.user_approver_a, req.pending_approver_ids)

        self._expire(req)
        self.Request._cron_check_escalations()
        req.invalidate_recordset()

        # Still pending on the same tier but now assigned to fallback
        self.assertEqual(req.current_tier_id.name, "T1")
        self.assertIn(self.user_fallback, req.pending_approver_ids)
        self.assertNotIn(self.user_approver_a, req.pending_approver_ids)
