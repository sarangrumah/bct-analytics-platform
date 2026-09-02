# -*- coding: utf-8 -*-
"""Webhook handler: verify-token + statuses + inbound messages."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestWebhook(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["whatsapp.account"]
        cls.Message = cls.env["whatsapp.message"]

        cls.account = cls.Account.create(
            {
                "name": "Test WA Account",
                "provider": "meta_cloud",
                "phone_number_id": "1234567890",
                "business_account_id": "9876543210",
                "access_token": "fake-token",
                "webhook_verify_token": "verify-secret-xyz",
                "sandbox_mode": True,
                "is_active": True,
            }
        )

    def test_status_update_delivered(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "ping",
                "provider_message_id": "wamid.ABC123",
                "state": "sent",
            }
        )
        ok = self.Message._apply_status_update(
            {
                "id": "wamid.ABC123",
                "status": "delivered",
                "timestamp": "1700000000",
            }
        )
        self.assertTrue(ok)
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "delivered")

    def test_status_update_failed_records_error(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "ping",
                "provider_message_id": "wamid.FAIL",
                "state": "sent",
            }
        )
        self.Message._apply_status_update(
            {
                "id": "wamid.FAIL",
                "status": "failed",
                "errors": [{"code": 131000, "title": "Generic error"}],
            }
        )
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "failed")
        self.assertIn("131000", msg.error_message or "")

    def test_status_update_unknown_wamid_returns_false(self):
        ok = self.Message._apply_status_update(
            {
                "id": "wamid.UNKNOWN",
                "status": "read",
            }
        )
        self.assertFalse(ok)

    def test_inbound_message_creates_record(self):
        new_msg = self.Message._record_inbound(
            self.account,
            {
                "from": "6281234567890",
                "id": "wamid.IN1",
                "timestamp": "1700000000",
                "type": "text",
                "text": {"body": "hello there"},
            },
        )
        self.assertTrue(new_msg)
        self.assertEqual(new_msg.direction, "inbound")
        self.assertEqual(new_msg.state, "received")
        self.assertEqual(new_msg.body, "hello there")
        self.assertEqual(new_msg.provider_message_id, "wamid.IN1")
        self.assertEqual(new_msg.to_phone, "6281234567890")

    def test_inbound_non_text_message_gets_placeholder(self):
        new_msg = self.Message._record_inbound(
            self.account,
            {
                "from": "6281234567890",
                "id": "wamid.IMG1",
                "type": "image",
                "image": {"id": "media-id"},
            },
        )
        self.assertEqual(new_msg.body, "[image message]")

    def test_inbound_matches_partner_by_phone(self):
        """Odoo 19 dropped res.partner.mobile.

        The matcher used to name it in the domain, which raises ValueError
        rather than simply not matching, so every inbound message blew up.
        """
        partner = self.env["res.partner"].create({"name": "Inbound Customer", "phone": "+62 812-3456-7890"})
        msg = self.Message._record_inbound(
            self.account,
            {
                "from": "6281234567890",
                "id": "wamid.INBOUND1",
                "type": "text",
                "text": {"body": "halo"},
            },
        )
        self.assertEqual(msg.to_partner_id, partner)
        self.assertEqual(msg.direction, "inbound")

    def test_inbound_without_a_matching_partner_still_records(self):
        """An unknown number must store the message, not raise."""
        msg = self.Message._record_inbound(
            self.account,
            {
                "from": "6289999999999",
                "id": "wamid.INBOUND2",
                "type": "text",
                "text": {"body": "siapa ini"},
            },
        )
        self.assertFalse(msg.to_partner_id)
        self.assertEqual(msg.direction, "inbound")

    def test_whatsapp_phone_helper_prefers_a_present_field(self):
        Partner = self.env["res.partner"]
        self.assertNotIn("mobile", Partner._fields, "Odoo 19 should not have res.partner.mobile")
        p = Partner.create({"name": "Phone Only", "phone": " +6281100002222 "})
        self.assertEqual(p._whatsapp_phone(), "+6281100002222")
        blank = Partner.create({"name": "No Phone"})
        self.assertEqual(blank._whatsapp_phone(), "")

    def test_inbound_matches_a_formatted_number(self):
        """`phone` holds what the user typed; separators must not defeat matching."""
        partner = self.env["res.partner"].create({"name": "Formatted Customer", "phone": "+62 811-2222-3333"})
        msg = self.Message._record_inbound(
            self.account,
            {
                "from": "6281122223333",
                "id": "wamid.INBOUND3",
                "type": "text",
                "text": {"body": "tes format"},
            },
        )
        self.assertEqual(msg.to_partner_id, partner)
