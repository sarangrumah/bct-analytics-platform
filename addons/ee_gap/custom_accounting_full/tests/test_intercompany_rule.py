# -*- coding: utf-8 -*-
"""Intercompany rule lookup + mapping behaviour."""

from __future__ import annotations

from odoo.exceptions import ValidationError

from .common import AccountingFullCommon


class TestIntercompanyRule(AccountingFullCommon):
    def test_cannot_target_same_company(self):
        with self.assertRaises(ValidationError):
            self.Rule.create(
                {
                    "name": "Self loop",
                    "company_from_id": self.company_a.id,
                    "company_to_id": self.company_a.id,
                }
            )

    def test_map_account_uses_explicit_mapping_first(self):
        rule = self.Rule.create(
            {
                "name": "A→B sale",
                "company_from_id": self.company_a.id,
                "company_to_id": self.company_b.id,
                "direction": "sale_to_purchase",
            }
        )
        self.Mapping.create(
            {
                "rule_id": rule.id,
                "source_account_id": self.rev_account_a.id,
                "target_account_id": self.exp_account_b.id,
            }
        )
        mapped = rule._map_account(self.rev_account_a)
        self.assertEqual(mapped, self.exp_account_b)

    def test_map_account_falls_back_to_same_code(self):
        rule = self.Rule.create(
            {
                "name": "A→B sale",
                "company_from_id": self.company_a.id,
                "company_to_id": self.company_b.id,
            }
        )
        # No explicit mapping: should match by code (both have 11100)
        mapped = rule._map_account(self.rec_account_a)
        self.assertEqual(mapped, self.rec_account_b)

    def test_mapping_constraints_cross_company(self):
        rule = self.Rule.create(
            {
                "name": "A→B sale",
                "company_from_id": self.company_a.id,
                "company_to_id": self.company_b.id,
            }
        )
        # Source account belongs to Company B, not Company A — invalid
        with self.assertRaises(ValidationError):
            self.Mapping.create(
                {
                    "rule_id": rule.id,
                    "source_account_id": self.rec_account_b.id,
                    "target_account_id": self.rec_account_a.id,
                }
            )
