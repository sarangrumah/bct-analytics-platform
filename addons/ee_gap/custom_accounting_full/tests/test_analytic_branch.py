# -*- coding: utf-8 -*-
"""Analytic branch dimension propagation through hierarchies."""

from __future__ import annotations

from .common import AccountingFullCommon


class TestAnalyticBranch(AccountingFullCommon):
    def test_root_self_references_as_branch_root(self):
        Plan = self.env["account.analytic.plan"]
        plan = Plan.create({"name": "Test Plan"})
        Analytic = self.env["account.analytic.account"]
        root = Analytic.create(
            {
                "name": "Jakarta HQ",
                "plan_id": plan.id,
                "x_custom_branch_code": "JKT-01",
                "x_custom_is_branch_root": True,
            }
        )
        self.assertEqual(root.x_custom_branch_root_id, root)

    def test_child_inherits_branch_root(self):
        Plan = self.env["account.analytic.plan"]
        plan = Plan.create({"name": "Test Plan"})
        Analytic = self.env["account.analytic.account"]
        root = Analytic.create(
            {
                "name": "Jakarta HQ",
                "plan_id": plan.id,
                "x_custom_branch_code": "JKT-01",
                "x_custom_is_branch_root": True,
            }
        )
        child = Analytic.create(
            {
                "name": "JKT Dept Finance",
                "plan_id": plan.id,
                "x_custom_parent_id": root.id,
            }
        )
        self.assertEqual(child.x_custom_branch_root_id, root)

    def test_orphan_root_returns_no_branch(self):
        Plan = self.env["account.analytic.plan"]
        plan = Plan.create({"name": "Test Plan"})
        Analytic = self.env["account.analytic.account"]
        non_branch = Analytic.create(
            {
                "name": "Standalone Project",
                "plan_id": plan.id,
            }
        )
        self.assertFalse(non_branch.x_custom_branch_root_id)
