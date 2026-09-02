# -*- coding: utf-8 -*-
"""Consolidation engine — balance aggregation + eliminations."""

from __future__ import annotations

from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import AccountingFullCommon


@tagged("post_install", "-at_install")
class TestConsolidation(AccountingFullCommon):
    def _post_simple_journal(self, company, debit_account, credit_account, amount, journal):
        """Post a manual journal entry (skips the invoice machinery)."""
        move = self.Move.with_company(company).create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "date": date.today(),
                "company_id": company.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Debit",
                            "account_id": debit_account.id,
                            "debit": amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": "Credit",
                            "account_id": credit_account.id,
                            "debit": 0.0,
                            "credit": amount,
                        },
                    ),
                ],
            }
        )
        move.action_post()
        return move

    def test_perimeter_includes_parent_and_subs(self):
        config = self.ConsolConfig.create(
            {
                "name": "FY",
                "fiscal_year": date.today().year,
                "parent_company_id": self.company_a.id,
                "subsidiary_ids": [(6, 0, [self.company_b.id])],
            }
        )
        self.assertEqual(config.perimeter_company_ids(), self.company_a | self.company_b)

    def test_parent_in_subsidiaries_rejected(self):
        with self.assertRaises(ValidationError):
            self.ConsolConfig.create(
                {
                    "name": "Bad",
                    "fiscal_year": date.today().year,
                    "parent_company_id": self.company_a.id,
                    "subsidiary_ids": [(6, 0, [self.company_a.id])],
                }
            )

    def test_trial_balance_aggregates_both_companies(self):
        # Post 1,000,000 in Co A and 500,000 in Co B on the same code (11100 → 41000)
        # so we know both should show up
        # Manual moves use a generic journal — create one for each company:
        misc_a = self._mk_journal(self.company_a, "Misc A", "general", "MISC-A")
        misc_b = self._mk_journal(self.company_b, "Misc B", "general", "MISC-B")
        self._post_simple_journal(self.company_a, self.rec_account_a, self.rev_account_a, 1_000_000, misc_a)
        self._post_simple_journal(self.company_b, self.rec_account_b, self.rev_account_b, 500_000, misc_b)

        config = self.ConsolConfig.create(
            {
                "name": "FY",
                "fiscal_year": date.today().year,
                "parent_company_id": self.company_a.id,
                "subsidiary_ids": [(6, 0, [self.company_b.id])],
            }
        )
        data = config.build_trial_balance(date.today().replace(month=1, day=1), date.today())
        accounts_by_code = {a["account_code"]: a for a in data["accounts"]}
        self.assertIn("11100", accounts_by_code)
        self.assertIn("41000", accounts_by_code)

        # 11100 should have both companies' balances summed in 'consolidated'
        rec_row = accounts_by_code["11100"]
        self.assertEqual(rec_row["by_company"][self.company_a.id], 1_000_000)
        self.assertEqual(rec_row["by_company"][self.company_b.id], 500_000)
        self.assertEqual(rec_row["elimination"], 0.0)
        self.assertEqual(rec_row["consolidated"], 1_500_000)

    def test_elimination_reduces_consolidated_total(self):
        # Build an intercompany pair on the IC clearing accounts that cancel:
        #   Co A: DR 11150 IC-Recv 200, CR 41000 Pendapatan 200  (intercompany sale)
        #   Co B: DR 52000 Pembelian 200, CR 21150 IC-Pay 200    (intercompany purchase)
        misc_a = self._mk_journal(self.company_a, "Misc A", "general", "MISC-A2")
        misc_b = self._mk_journal(self.company_b, "Misc B", "general", "MISC-B2")
        self._post_simple_journal(self.company_a, self.ic_recv_a, self.rev_account_a, 200, misc_a)
        self._post_simple_journal(self.company_b, self.exp_account_b, self.ic_pay_b, 200, misc_b)

        config = self.ConsolConfig.create(
            {
                "name": "FY",
                "fiscal_year": date.today().year,
                "parent_company_id": self.company_a.id,
                "subsidiary_ids": [(6, 0, [self.company_b.id])],
                "elimination_account_ids": [(6, 0, [self.ic_recv_a.id, self.ic_pay_b.id])],
            }
        )
        data = config.build_trial_balance(date.today().replace(month=1, day=1), date.today())
        by_code = {a["account_code"]: a for a in data["accounts"]}
        # IC receivable in A should have 200, eliminated to 0
        self.assertEqual(by_code["11150"]["by_company"][self.company_a.id], 200)
        self.assertAlmostEqual(by_code["11150"]["consolidated"], 0.0)
        # IC payable in B: balance -200 (credit), eliminated back to 0
        self.assertAlmostEqual(by_code["21150"]["consolidated"], 0.0)
