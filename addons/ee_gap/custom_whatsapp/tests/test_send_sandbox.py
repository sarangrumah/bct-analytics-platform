# -*- coding: utf-8 -*-
"""Sandbox-mode send: no real HTTP, fake message_id, state transitions."""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestSendSandbox(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["whatsapp.account"]
        cls.Message = cls.env["whatsapp.message"]
        cls.Template = cls.env["whatsapp.template"]

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

    def test_sandbox_send_marks_sent_with_fake_id(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "hello from sandbox",
                "direction": "outbound",
            }
        )
        msg.action_send()
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "sent")
        self.assertTrue(msg.provider_message_id)
        self.assertTrue(msg.provider_message_id.startswith("sandbox-"))
        self.assertTrue(msg.sent_at)
        self.assertFalse(msg.error_message)

    def test_sandbox_send_skips_inbound(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "incoming",
                "direction": "inbound",
                "state": "received",
            }
        )
        # action_send must be a no-op for inbound rows.
        msg.action_send()
        self.assertEqual(msg.state, "received")

    def test_inactive_account_marks_failed(self):
        self.account.is_active = False
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "ping",
            }
        )
        msg.action_send()
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "failed")
        self.assertIn("inactive", (msg.error_message or "").lower())

    def test_template_payload_uses_template_type_when_approved(self):
        tpl = self.Template.create(
            {
                "name": "order_shipped_id",
                "account_id": self.account.id,
                "language_code": "id",
                "category": "utility",
                "body_text": "Halo {{1}}, pesanan {{2}} sudah dikirim.",
                "status": "approved",
            }
        )
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "template_id": tpl.id,
                "to_phone": "+6281234567890",
            }
        )
        payload = msg._build_payload()
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["name"], "order_shipped_id")
        self.assertEqual(payload["template"]["language"]["code"], "id")
        # E.164 plus should be stripped per Meta convention.
        self.assertEqual(payload["to"], "6281234567890")

    def test_text_payload_when_no_approved_template(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "free text",
            }
        )
        payload = msg._build_payload()
        self.assertEqual(payload["type"], "text")
        self.assertEqual(payload["text"]["body"], "free text")

    def test_sandbox_log_does_not_disclose_number_or_body(self):
        """Sandbox is exercised with real customer data during UAT.

        The Odoo log is not a sensitive store, so the recipient number and the
        message text must not land in it in the clear.

        The logger is patched rather than captured: Odoo runs tests at
        ``--log-level=test`` (level 25), which filters INFO records out before
        any handler sees them, so assertLogs would observe nothing.
        """
        from unittest.mock import patch

        from odoo.addons.custom_whatsapp.models import whatsapp_message as wm

        number = "+6281234567890"
        secret_body = "Kode OTP anda 998877, jangan dibagikan"
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": number,
                "body": secret_body,
                "direction": "outbound",
            }
        )

        emitted = []

        def _capture(fmt, *args):
            emitted.append(fmt % args)

        with patch.object(wm._logger, "info", _capture):
            msg.action_send()

        line = "\n".join(x for x in emitted if "[whatsapp sandbox]" in x)
        self.assertTrue(line, f"sandbox send logged nothing; saw {emitted!r}")

        self.assertNotIn(number, line)
        self.assertNotIn("6281234567890", line)
        self.assertNotIn("81234567890", line)
        self.assertNotIn(secret_body, line)
        self.assertNotIn("998877", line)
        # Still traceable: masked number, body length, record id.
        self.assertIn("62********890", line)
        self.assertIn(f"body_len={len(secret_body)}", line)
        self.assertIn(f"msg_id={msg.id}", line)

    def test_mask_phone_shapes(self):
        from odoo.addons.custom_whatsapp.models.whatsapp_message import _mask_phone

        self.assertEqual(_mask_phone("+6281234567890"), "62********890")
        self.assertEqual(_mask_phone("081234567890"), "08*******890")
        self.assertEqual(_mask_phone("+1 (555) 010-9999"), "15******999")
        # Too short to mask meaningfully -> disclose nothing at all.
        self.assertEqual(_mask_phone("12345"), "*****")
        self.assertEqual(_mask_phone(""), "(none)")
        self.assertEqual(_mask_phone(None), "(none)")
