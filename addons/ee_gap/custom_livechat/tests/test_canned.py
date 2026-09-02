# -*- coding: utf-8 -*-
"""Tests for the canned response shortcut expansion."""

from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_livechat")
class TestCannedResponseExpand(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Canned = cls.env["custom.livechat.canned.response"]
        cls.greet = cls.Canned.create(
            {
                "name": "Greeting",
                "shortcut": "hi",
                "body": "<p>Hello there!</p>",
                "category": "Greeting",
            }
        )

    def test_expand_canned_found_increments_usage(self):
        before = self.greet.times_used
        result = self.Canned.expand_canned("hi")
        self.assertTrue(result["found"])
        self.assertEqual(result["body"], "<p>Hello there!</p>")
        self.assertEqual(result["shortcut"], "hi")
        self.greet.invalidate_recordset(["times_used"])
        self.assertEqual(self.greet.times_used, before + 1)

    def test_expand_canned_strips_leading_colon(self):
        result = self.Canned.expand_canned(":hi")
        self.assertTrue(result["found"])
        self.assertEqual(result["shortcut"], "hi")

    def test_expand_canned_not_found(self):
        result = self.Canned.expand_canned("nope_unknown")
        self.assertFalse(result["found"])
        self.assertEqual(result["body"], "")

    def test_expand_canned_empty_input(self):
        result = self.Canned.expand_canned("")
        self.assertFalse(result["found"])
        result_none = self.Canned.expand_canned(None)
        self.assertFalse(result_none["found"])

    def test_expand_canned_inactive_ignored(self):
        self.greet.active = False
        result = self.Canned.expand_canned("hi")
        self.assertFalse(result["found"])
