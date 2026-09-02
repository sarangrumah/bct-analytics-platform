# -*- coding: utf-8 -*-
"""Tests for the dedup scan algorithm."""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_data_cleaning")
class TestDedupScanPhones(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env["res.partner"]
        cls.Rule = cls.env["custom.dedup.rule"]
        cls.Candidate = cls.env["custom.dedup.candidate"]

        # Three records that should collapse to a single duplicate group
        # after Indonesian phone normalization.
        cls.p1 = cls.Partner.create(
            {
                "name": "Dedup Test 1",
                "phone": "0812-3456-7890",
            }
        )
        cls.p2 = cls.Partner.create(
            {
                "name": "Dedup Test 2",
                "phone": "+62 812 3456 7890",
            }
        )
        cls.p3 = cls.Partner.create(
            {
                "name": "Dedup Test 3",
                "phone": "62812 3456 7890",
            }
        )
        # A non-duplicate
        cls.p4 = cls.Partner.create(
            {
                "name": "Dedup Test Unique",
                "phone": "081299999999",
            }
        )

    def test_dedup_scan_phones(self):
        rule = self.Rule.create(
            {
                "name": "Test Phone Dedup",
                "model_name": "res.partner",
                "match_fields": "phone",
                "normalize_phone_id": True,
            }
        )
        rule.action_run_scan()
        # We should have at least one candidate
        cands = self.Candidate.search([("rule_id", "=", rule.id)])
        self.assertTrue(cands, "Scan produced no candidates")
        # And among them, one whose IDs include our 3 duplicates
        target_ids = {self.p1.id, self.p2.id, self.p3.id}
        found = False
        for c in cands:
            ids = set(json.loads(c.res_ids_json))
            if target_ids.issubset(ids):
                found = True
                break
        self.assertTrue(found, "Did not find expected duplicate group %s" % target_ids)
        self.assertGreaterEqual(rule.last_match_count, 1)
        self.assertTrue(rule.last_run_at)

    def test_rescan_is_idempotent(self):
        """Running twice should not pile up duplicate pending candidates."""
        rule = self.Rule.create(
            {
                "name": "Test Idempotent Scan",
                "model_name": "res.partner",
                "match_fields": "phone",
                "normalize_phone_id": True,
            }
        )
        rule.action_run_scan()
        first_count = self.Candidate.search_count([("rule_id", "=", rule.id)])
        rule.action_run_scan()
        second_count = self.Candidate.search_count([("rule_id", "=", rule.id)])
        self.assertEqual(first_count, second_count)
