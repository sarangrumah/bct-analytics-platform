# -*- coding: utf-8 -*-
"""Matrix priority + condition_domain resolution."""

from __future__ import annotations

from .common import ApprovalTestCommon


class TestMatrixResolution(ApprovalTestCommon):
    def test_no_active_matrix_returns_none(self):
        po = self._make_po()
        matrix = self.Matrix._resolve_for(po)
        self.assertFalse(matrix)

    def test_single_matrix_no_condition_resolves(self):
        m = self._make_matrix("All POs", priority=10, condition_domain="[]")
        self._add_tier(m, approvers=[self.user_approver_a])
        po = self._make_po()
        resolved = self.Matrix._resolve_for(po)
        self.assertEqual(resolved, m)

    def test_higher_priority_wins(self):
        low = self._make_matrix("Low priority", priority=5, condition_domain="[]")
        self._add_tier(low, approvers=[self.user_approver_a])
        high = self._make_matrix("High priority", priority=50, condition_domain="[]")
        self._add_tier(high, approvers=[self.user_approver_b])
        po = self._make_po()
        resolved = self.Matrix._resolve_for(po)
        self.assertEqual(resolved, high)

    def test_condition_domain_filters(self):
        # Domain on amount_total: a PO with no lines = 0, so this won't match
        m = self._make_matrix("Big POs", priority=10, condition_domain="[('amount_total','>',100000)]")
        self._add_tier(m, approvers=[self.user_approver_a])
        po = self._make_po()
        self.assertFalse(self.Matrix._resolve_for(po))

    def test_archived_matrix_skipped(self):
        m = self._make_matrix("Inactive", priority=99, condition_domain="[]")
        self._add_tier(m, approvers=[self.user_approver_a])
        m.active = False
        po = self._make_po()
        self.assertFalse(self.Matrix._resolve_for(po))
