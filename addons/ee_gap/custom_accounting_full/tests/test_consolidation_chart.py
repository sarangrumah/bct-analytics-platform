# -*- coding: utf-8 -*-
"""Tests for the consolidation chart / mapping / elimination dry-run."""

from __future__ import annotations

from datetime import date, timedelta

from odoo.tests import tagged

from .common import AccountingFullCommon


@tagged("post_install", "-at_install")
class TestConsolidationChart(AccountingFullCommon):
    def setUp(self):
        super().setUp()
        self.chart = self.env["custom.consolidation.chart"].create(
            {
                "name": "Group IFRS",
                "code": "GRP",
                "currency_id": self.env.ref("base.IDR").id,
                "company_ids": [(6, 0, [self.company_a.id, self.company_b.id])],
            }
        )

    def test_create_chart_accounts(self):
        ChartAccount = self.env["custom.consolidation.chart.account"]
        a = ChartAccount.create(
            {
                "chart_id": self.chart.id,
                "code": "1000",
                "name": "Cash & Equivalents",
                "account_category": "asset",
            }
        )
        b = ChartAccount.create(
            {
                "chart_id": self.chart.id,
                "code": "4000",
                "name": "Group Revenue",
                "account_category": "income",
            }
        )
        self.assertEqual(len(self.chart.account_ids), 2)
        self.assertEqual(a.account_category, "asset")
        self.assertEqual(b.account_category, "income")

    def test_create_mapping(self):
        target = self.env["custom.consolidation.chart.account"].create(
            {
                "chart_id": self.chart.id,
                "code": "4000",
                "name": "Revenue",
                "account_category": "income",
            }
        )
        mapping = self.env["custom.consolidation.mapping"].create(
            {
                "chart_id": self.chart.id,
                "company_id": self.company_a.id,
                "source_account_id": self.rev_account_a.id,
                "target_account_id": target.id,
                "fx_method": "closing",
                "weight": 1.0,
            }
        )
        self.assertEqual(mapping.target_account_id, target)

    def test_elimination_proposal_dry_run(self):
        rule = self.env["custom.elimination.rule"].create(
            {
                "name": "IC Revenue/Cost",
                "chart_id": self.chart.id,
                "company_a_id": self.company_a.id,
                "company_b_id": self.company_b.id,
                "account_a_id": self.rev_account_a.id,
                "account_b_id": self.exp_account_b.id,
                "match_type": "exact",
            }
        )
        proposal = self.env["custom.elimination.proposal"].create(
            {
                "chart_id": self.chart.id,
                "rule_id": rule.id,
                "date_from": date.today() - timedelta(days=30),
                "date_to": date.today(),
            }
        )
        proposal.action_compute()
        # Even with no posted moves, compute should succeed → proposed state
        self.assertEqual(proposal.state, "proposed")
        self.assertEqual(proposal.total_amount, 0.0)
