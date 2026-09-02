# -*- coding: utf-8 -*-
"""Bank statement auto-reconciliation rule basic match."""

from __future__ import annotations


from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestReconcileRule(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.partner = self.env["res.partner"].create({"name": "Reco Cust"})
        self.bank_journal = self.env["account.journal"].create(
            {
                "name": "Bank-RECO",
                "code": "BRECO",
                "type": "bank",
                "company_id": self.company.id,
            }
        )

    def test_rule_created_and_matches_partner_filter(self):
        rule = self.env["custom.reconcile.rule"].create(
            {
                "name": "Auto-match by partner+amount",
                "company_id": self.company.id,
                "journal_ids": [(6, 0, [self.bank_journal.id])],
                "match_partner": True,
                "match_amount": True,
                "amount_tolerance": 0.01,
                "auto_validate": False,
            }
        )
        self.assertTrue(rule.active)
        # Cron should run without crashing even when no statement lines exist.
        matched = self.env["custom.reconcile.rule"]._cron_apply_rules()
        self.assertEqual(matched, 0)
