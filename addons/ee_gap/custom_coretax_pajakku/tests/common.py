# -*- coding: utf-8 -*-
"""Shared fixtures: enabled config + sandbox + mocked client_secret accessor."""

from __future__ import annotations

from unittest import mock

from odoo.tests.common import TransactionCase


class PajakkuCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["custom.coretax.config"]
        cls.Adapter = cls.env["custom.coretax.adapter.pajakku"]
        cls.Tx = cls.env["custom.coretax.transaction"]
        cls.Usage = cls.env["custom.coretax.pajakku.usage"]

        cls.config = cls.Config.create(
            {
                "name": "Test Coretax + Pajakku",
                "npwp": "012345678901234",
                "taxpayer_name": "PT Test Pajakku",
                "kpp_code": "999",
                "adapter_type": "pajakku",
                "pajakku_enabled": True,
                "pajakku_sandbox_mode": True,
                "pajakku_client_id": "test-client-id",
                "pajakku_api_url": "https://sandbox-api.pajakku.test",
            }
        )

        # Stub the encrypted-secret accessor so tests don't need a real KMS key
        cls._secret_patch = mock.patch.object(
            type(cls.config),
            "_pajakku_get_client_secret",
            return_value="test-client-secret",
        )
        cls._secret_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._secret_patch.stop()
        super().tearDownClass()

    def _make_response(
        self, status_code: int = 200, json_body: dict | None = None, content: bytes = b"", headers: dict | None = None
    ):
        resp = mock.MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body or {}
        resp.content = content or (__import__("json").dumps(json_body or {}).encode() if json_body else b"")
        resp.headers = headers or {}
        resp.text = (resp.content or b"").decode("utf-8", errors="replace")
        return resp
