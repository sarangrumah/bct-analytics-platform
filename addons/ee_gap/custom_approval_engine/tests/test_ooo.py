# -*- coding: utf-8 -*-
"""Out-of-office auto-delegation."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields

from .common import ApprovalTestCommon


class TestOOO(ApprovalTestCommon):
    def test_active_ooo_with_target_redirects_approver(self):
        now = fields.Datetime.now()
        self.OOO.create(
            {
                "user_id": self.user_approver_a.id,
                "date_from": now - timedelta(hours=2),
                "date_to": now + timedelta(hours=2),
                "auto_delegate_to_id": self.user_delegate.id,
                "note": "Off-site workshop",
            }
        )

        m = self._make_matrix("Single tier")
        self._add_tier(m, approvers=[self.user_approver_a])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self.assertIn(self.user_delegate, req.pending_approver_ids)
        self.assertNotIn(self.user_approver_a, req.pending_approver_ids)

    def test_ooo_without_target_keeps_original_approver(self):
        now = fields.Datetime.now()
        self.OOO.create(
            {
                "user_id": self.user_approver_a.id,
                "date_from": now - timedelta(hours=1),
                "date_to": now + timedelta(hours=1),
                "auto_delegate_to_id": False,
            }
        )

        m = self._make_matrix("Single tier")
        self._add_tier(m, approvers=[self.user_approver_a])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self.assertIn(self.user_approver_a, req.pending_approver_ids)

    def test_inactive_ooo_does_not_redirect(self):
        now = fields.Datetime.now()
        self.OOO.create(
            {
                "user_id": self.user_approver_a.id,
                "date_from": now - timedelta(hours=2),
                "date_to": now + timedelta(hours=2),
                "auto_delegate_to_id": self.user_delegate.id,
                "active": False,
            }
        )

        m = self._make_matrix("Single tier")
        self._add_tier(m, approvers=[self.user_approver_a])
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self.assertIn(self.user_approver_a, req.pending_approver_ids)
