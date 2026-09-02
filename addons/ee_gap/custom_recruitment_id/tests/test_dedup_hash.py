# -*- coding: utf-8 -*-
"""Dedup hash + duplicate-flagging behavior on hr.applicant."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.hr_applicant import _compute_dedup_hash, _normalize_phone


@tagged("post_install", "-at_install")
class TestDedupHash(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Applicant = cls.env["hr.applicant"]

    def test_normalize_phone_strips_punctuation(self):
        self.assertEqual(_normalize_phone("(0812) 3456-7890"), "6281234567890")
        self.assertEqual(_normalize_phone("+62 812 3456 7890"), "6281234567890")
        self.assertEqual(_normalize_phone("0812.3456.7890"), "6281234567890")
        self.assertEqual(_normalize_phone(None), "")

    def test_dedup_hash_stable_across_case_and_format(self):
        a = _compute_dedup_hash("Foo@Example.com", "+62 812 3456 7890")
        b = _compute_dedup_hash("foo@example.com", "0812-3456-7890")
        self.assertTrue(a)
        self.assertEqual(a, b)

    def test_dedup_hash_falsy_when_no_email_no_phone(self):
        self.assertFalse(_compute_dedup_hash("", ""))
        self.assertFalse(_compute_dedup_hash(None, None))

    def test_second_applicant_flagged_as_duplicate(self):
        a1 = self.Applicant.create(
            {
                "partner_name": "Budi Santoso",
                "email_from": "budi@example.com",
                "partner_phone": "+62 812 1111 2222",
            }
        )
        a2 = self.Applicant.create(
            {
                "partner_name": "Budi S.",
                "email_from": "BUDI@example.com",
                "partner_phone": "0812-1111-2222",
            }
        )
        # Refresh computed/store fields.
        a1.invalidate_recordset()
        a2.invalidate_recordset()
        self.assertTrue(a1.x_dedup_hash)
        self.assertEqual(a1.x_dedup_hash, a2.x_dedup_hash)
        self.assertFalse(a1.x_is_duplicate)
        self.assertTrue(a2.x_is_duplicate)
        self.assertEqual(a2.x_duplicate_of, a1)

    def test_unrelated_applicants_not_flagged(self):
        a1 = self.Applicant.create(
            {
                "partner_name": "Ani",
                "email_from": "ani@example.com",
                "partner_phone": "+62 812 9999 0000",
            }
        )
        a2 = self.Applicant.create(
            {
                "partner_name": "Citra",
                "email_from": "citra@example.com",
                "partner_phone": "+62 812 3333 4444",
            }
        )
        a1.invalidate_recordset()
        a2.invalidate_recordset()
        self.assertNotEqual(a1.x_dedup_hash, a2.x_dedup_hash)
        self.assertFalse(a1.x_is_duplicate)
        self.assertFalse(a2.x_is_duplicate)
