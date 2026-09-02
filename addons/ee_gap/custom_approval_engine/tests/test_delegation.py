# -*- coding: utf-8 -*-
"""Manual delegation redirects pending approvers."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields

from .common import ApprovalTestCommon


class TestDelegation(ApprovalTestCommon):
    def _open_request_with_a_as_approver(self):
        m = self._make_matrix("Single tier")
        self._add_tier(m, name="A only", approvers=[self.user_approver_a])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()
        return req

    def test_active_delegation_replaces_approver(self):
        now = fields.Datetime.now()
        self.Delegation.create(
            {
                "user_id": self.user_approver_a.id,
                "delegate_to_id": self.user_delegate.id,
                "valid_from": now - timedelta(hours=1),
                "valid_until": now + timedelta(hours=1),
                "reason": "Sick leave",
            }
        )

        req = self._open_request_with_a_as_approver()
        # Pending list resolved at submit time should already reflect delegation
        self.assertIn(self.user_delegate, req.pending_approver_ids)
        self.assertNotIn(self.user_approver_a, req.pending_approver_ids)

    def test_expired_delegation_does_not_apply(self):
        past = fields.Datetime.now() - timedelta(days=7)
        self.Delegation.create(
            {
                "user_id": self.user_approver_a.id,
                "delegate_to_id": self.user_delegate.id,
                "valid_from": past - timedelta(hours=1),
                "valid_until": past,
                "reason": "Old",
            }
        )

        req = self._open_request_with_a_as_approver()
        self.assertIn(self.user_approver_a, req.pending_approver_ids)
        self.assertNotIn(self.user_delegate, req.pending_approver_ids)

    def test_inactive_delegation_does_not_apply(self):
        now = fields.Datetime.now()
        d = self.Delegation.create(
            {
                "user_id": self.user_approver_a.id,
                "delegate_to_id": self.user_delegate.id,
                "valid_from": now - timedelta(hours=1),
                "valid_until": now + timedelta(hours=1),
                "active": False,
            }
        )
        self.assertFalse(d.active)

        req = self._open_request_with_a_as_approver()
        self.assertIn(self.user_approver_a, req.pending_approver_ids)

    def test_delegation_attribution_in_history(self):
        now = fields.Datetime.now()
        self.Delegation.create(
            {
                "user_id": self.user_approver_a.id,
                "delegate_to_id": self.user_delegate.id,
                "valid_from": now - timedelta(hours=1),
                "valid_until": now + timedelta(hours=1),
            }
        )

        req = self._open_request_with_a_as_approver()
        # Delegate approves on behalf of A
        req.with_user(self.user_delegate).action_approve(comment="ack via delegation")
        req.invalidate_recordset()

        line = req.history_ids.filtered(lambda l: l.action == "approved")
        self.assertEqual(line.delegated_from_id, self.user_approver_a)
