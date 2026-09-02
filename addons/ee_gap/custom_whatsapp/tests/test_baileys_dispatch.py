# -*- coding: utf-8 -*-
"""Baileys provider: payload shape, dispatch routing, and webhook HMAC validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, HttpCase

from odoo.addons.custom_whatsapp.controllers.main import _baileys_signature_valid


@tagged("post_install", "-at_install")
class TestBaileysPayload(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["whatsapp.account"]
        cls.Message = cls.env["whatsapp.message"]
        cls.account = cls.Account.create(
            {
                "name": "Baileys Test",
                "provider": "baileys",
                "baileys_sidecar_url": "http://baileys:8088",
                "baileys_shared_secret": "test-secret",
                "baileys_session_id": "acct-test",
                "sandbox_mode": False,
                "is_active": True,
            }
        )

    def test_build_baileys_payload_text(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "halo",
            }
        )
        payload = msg._build_baileys_payload()
        self.assertEqual(payload["to"], "6281234567890")
        self.assertEqual(payload["type"], "text")
        self.assertEqual(payload["text"], "halo")

    def test_do_send_routes_to_baileys_post(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "portal link",
            }
        )

        captured = {}

        def fake_post(self_account, path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"id": "baileys.MSG.123"}

        with patch.object(type(self.account), "_baileys_post", fake_post):
            msg._do_send()
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "sent")
        self.assertEqual(msg.provider_message_id, "baileys.MSG.123")
        self.assertEqual(captured["path"], "sessions/acct-test/messages")
        self.assertEqual(captured["payload"]["text"], "portal link")

    def test_do_send_records_failure_when_no_id(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "broken",
            }
        )

        def fake_post(self_account, path, payload):
            return {}  # missing id

        with patch.object(type(self.account), "_baileys_post", fake_post):
            msg._do_send()
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "failed")
        self.assertIn("id", (msg.error_message or "").lower())


@tagged("post_install", "-at_install")
class TestBaileysSignature(TransactionCase):
    def test_valid_signature_passes(self):
        secret = "shhh"
        body = b'{"x":1}'
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        header = f"sha256={digest}"
        self.assertTrue(_baileys_signature_valid(secret, body, header))

    def test_bad_signature_fails(self):
        self.assertFalse(_baileys_signature_valid("shhh", b"{}", "sha256=deadbeef"))

    def test_missing_header_fails(self):
        self.assertFalse(_baileys_signature_valid("shhh", b"{}", ""))

    def test_wrong_secret_fails(self):
        body = b"{}"
        digest = hmac.new(b"other", body, hashlib.sha256).hexdigest()
        self.assertFalse(_baileys_signature_valid("shhh", body, f"sha256={digest}"))


@tagged("post_install", "-at_install")
class TestBaileysWebhookDispatch(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["whatsapp.account"]
        cls.Message = cls.env["whatsapp.message"]
        cls.account = cls.Account.create(
            {
                "name": "Baileys Webhook Test",
                "provider": "baileys",
                "baileys_sidecar_url": "http://baileys:8088",
                "baileys_shared_secret": "wh-secret",
                "baileys_session_id": "acct-wh",
                "sandbox_mode": False,
                "is_active": True,
            }
        )

    def _post_event(self, event_type, payload, secret=None, sign=True):
        secret_to_use = secret if secret is not None else self.account.baileys_shared_secret
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Baileys-Event": event_type}
        if sign:
            digest = hmac.new(secret_to_use.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Baileys-Signature"] = f"sha256={digest}"
        return self.url_open(
            f"/custom_whatsapp/webhook/{self.account.id}",
            data=body,
            headers=headers,
        )

    def test_status_update_flips_message_state(self):
        msg = self.Message.create(
            {
                "account_id": self.account.id,
                "to_phone": "+6281234567890",
                "body": "ping",
                "provider_message_id": "baileys.STATUS1",
                "state": "sent",
            }
        )
        resp = self._post_event(
            "status",
            {"session_id": "acct-wh", "id": "baileys.STATUS1", "status": "delivered"},
        )
        self.assertEqual(resp.status_code, 200)
        msg.invalidate_recordset()
        self.assertEqual(msg.state, "delivered")

    def test_connection_event_updates_account_status(self):
        resp = self._post_event(
            "connection",
            {"session_id": "acct-wh", "status": "connected", "phone": "6281234567890"},
        )
        self.assertEqual(resp.status_code, 200)
        self.account.invalidate_recordset()
        self.assertEqual(self.account.baileys_status, "connected")
        self.assertEqual(self.account.baileys_phone, "6281234567890")

    def test_inbound_message_creates_record(self):
        resp = self._post_event(
            "message",
            {
                "session_id": "acct-wh",
                "message": {
                    "id": "baileys.IN1",
                    "from": "6281234567890",
                    "type": "text",
                    "text": "halo balik",
                    "timestamp": 1700000000,
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        inbound = self.Message.search([("provider_message_id", "=", "baileys.IN1")], limit=1)
        self.assertTrue(inbound)
        self.assertEqual(inbound.direction, "inbound")
        self.assertEqual(inbound.state, "received")
        self.assertEqual(inbound.body, "halo balik")

    def test_bad_signature_rejected(self):
        resp = self._post_event(
            "status",
            {"id": "x", "status": "delivered"},
            secret="wrong-secret",
        )
        self.assertEqual(resp.status_code, 403)
