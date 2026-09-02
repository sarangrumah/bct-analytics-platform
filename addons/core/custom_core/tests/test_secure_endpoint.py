# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..controllers import secure_endpoint as se


def _sign(secret: str, body: bytes, ts: str) -> str:
    return hmac.new(secret.encode("utf-8"), ts.encode("utf-8") + body, hashlib.sha256).hexdigest()


class _FakeHttpReq:
    def __init__(self, body=b"", headers=None, remote="10.0.0.5", path="/test"):
        self._body = body
        self.headers = headers or {}
        self.remote_addr = remote
        self.environ = {}
        self.path = path

    def get_data(self):
        return self._body


class _FakeRequest:
    def __init__(self, env, body=b"", headers=None, remote="10.0.0.5"):
        self.env = env
        self.httprequest = _FakeHttpReq(body=body, headers=headers, remote=remote)
        self.responses = []

    def make_json_response(self, payload, status=200):
        self.responses.append((status, payload))
        return (status, payload)


@tagged("post_install", "-at_install")
class TestSecureEndpoint(TransactionCase):
    SCOPE = "unittest"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret = "dummy-hmac-key-for-tests"
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param(f"custom_core.secure_endpoint.{cls.SCOPE}.secret", cls.secret)
        ICP.set_param(f"custom_core.secure_endpoint.{cls.SCOPE}.allowed_cidrs", "10.0.0.0/24,127.0.0.1")

    def setUp(self):
        super().setUp()
        # Reset process-local nonce cache between tests.
        se._NONCE_CACHE.clear()
        se._NonceStore._redis_client = None
        se._NonceStore._redis_probed = True  # skip probe in tests

    def _invoke(self, body=b'{"a":1}', sig=None, ts=None, remote="10.0.0.5"):
        ts = ts if ts is not None else str(int(time.time()))
        sig = sig if sig is not None else _sign(self.secret, body, ts)
        fake = _FakeRequest(self.env, body=body, headers={"X-Signature": sig, "X-Timestamp": ts}, remote=remote)

        @se.secure_endpoint(self.SCOPE)
        def handler():
            return ("ok", {"ok": True})

        with patch.object(se, "request", fake):
            return handler(), fake

    def test_happy_path(self):
        result, fake = self._invoke()
        self.assertEqual(result, ("ok", {"ok": True}))

    def test_bad_signature(self):
        result, fake = self._invoke(sig="0" * 64)
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "BAD_SIGNATURE")

    def test_expired_timestamp(self):
        old_ts = str(int(time.time()) - 10_000)
        body = b'{"a":1}'
        sig = _sign(self.secret, body, old_ts)
        result, fake = self._invoke(body=body, sig=sig, ts=old_ts)
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "EXPIRED_TIMESTAMP")

    def test_replay_nonce(self):
        body = b'{"a":1}'
        ts = str(int(time.time()))
        sig = _sign(self.secret, body, ts)
        r1, _ = self._invoke(body=body, sig=sig, ts=ts)
        self.assertEqual(r1, ("ok", {"ok": True}))
        r2, _ = self._invoke(body=body, sig=sig, ts=ts)
        self.assertEqual(r2[0], 401)
        self.assertEqual(r2[1]["error_code"], "REPLAY_NONCE")

    def test_ip_not_whitelisted(self):
        result, fake = self._invoke(remote="8.8.8.8")
        self.assertEqual(result[0], 403)
        self.assertEqual(result[1]["error_code"], "IP_NOT_ALLOWED")

    def test_auth_mode_default_is_hmac(self):
        """No auth_mode param set -> the pre-existing HMAC path, unchanged.

        This is the non-regression guard for the five live scopes (hht, wms,
        finance_sap, storefront, ops_alertmanager), none of which set auth_mode.
        A correctly signed request must pass and an unsigned one must still 401 --
        adding api_key mode must not turn any of them into a static-key endpoint.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        self.assertFalse(ICP.get_param(f"custom_core.secure_endpoint.{self.SCOPE}.auth_mode"))
        self.assertEqual(self._invoke()[0], ("ok", {"ok": True}))

        # An api-key header must buy nothing while the scope is in hmac mode.
        fake = _FakeRequest(self.env, body=b'{"a":1}', headers={"X-API-Key": "anything"})

        @se.secure_endpoint(self.SCOPE)
        def handler():
            return ("ok", {"ok": True})

        with patch.object(se, "request", fake):
            result = handler()
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "MISSING_AUTH_HEADERS")


@tagged("post_install", "-at_install")
class TestSecureEndpointApiKey(TransactionCase):
    """api_key mode: static Bearer/X-API-Key auth with a mandatory CIDR allow-list.

    The contract asserted here is that the weaker credential is never accepted
    without its compensating control -- a scope with no allowed_cidrs must fail
    closed rather than serve every caller on the internet.
    """

    SCOPE = "unittest_apikey"
    KEY = "k3y-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param(f"custom_core.secure_endpoint.{cls.SCOPE}.auth_mode", "api_key")
        ICP.set_param(f"custom_core.secure_endpoint.{cls.SCOPE}.api_keys", cls.KEY)
        ICP.set_param(f"custom_core.secure_endpoint.{cls.SCOPE}.allowed_cidrs", "10.0.0.0/24")

    def setUp(self):
        super().setUp()
        se._NONCE_CACHE.clear()
        se._NonceStore._redis_client = None
        se._NonceStore._redis_probed = True

    def _invoke(self, headers=None, remote="10.0.0.5", body=b'{"a":1}', scope=None):
        fake = _FakeRequest(self.env, body=body, headers=headers or {}, remote=remote)

        @se.secure_endpoint(scope or self.SCOPE)
        def handler():
            return ("ok", {"ok": True})

        with patch.object(se, "request", fake):
            return handler(), fake

    def _param(self, suffix, value):
        self.env["ir.config_parameter"].sudo().set_param(f"custom_core.secure_endpoint.{self.SCOPE}.{suffix}", value)

    def test_bearer_accepted(self):
        result, _ = self._invoke(headers={"Authorization": f"Bearer {self.KEY}"})
        self.assertEqual(result, ("ok", {"ok": True}))

    def test_bearer_prefix_is_case_insensitive(self):
        result, _ = self._invoke(headers={"Authorization": f"bearer {self.KEY}"})
        self.assertEqual(result, ("ok", {"ok": True}))

    def test_x_api_key_header_accepted(self):
        result, _ = self._invoke(headers={"X-API-Key": self.KEY})
        self.assertEqual(result, ("ok", {"ok": True}))

    def test_wrong_key_rejected(self):
        result, _ = self._invoke(headers={"X-API-Key": "nope"})
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "BAD_API_KEY")

    def test_missing_key_rejected(self):
        result, _ = self._invoke(headers={})
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "MISSING_API_KEY")

    def test_signature_headers_do_not_authenticate(self):
        """A valid-looking HMAC pair must not bypass api_key mode."""
        ts = str(int(time.time()))
        result, _ = self._invoke(headers={"X-Signature": "0" * 64, "X-Timestamp": ts})
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "MISSING_API_KEY")

    def test_rotation_window_accepts_both_keys(self):
        self._param("api_keys", f"{self.KEY},new-key-bbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(self._invoke(headers={"X-API-Key": self.KEY})[0], ("ok", {"ok": True}))
        self.assertEqual(
            self._invoke(headers={"X-API-Key": "new-key-bbbbbbbbbbbbbbbbbbbbbbbb"})[0],
            ("ok", {"ok": True}),
        )

    def test_cidr_is_mandatory(self):
        """No allow-list in api_key mode must fail closed, not allow everyone."""
        self._param("allowed_cidrs", "")
        result, _ = self._invoke(headers={"X-API-Key": self.KEY}, remote="8.8.8.8")
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "NO_CIDR_CONFIGURED")

    def test_no_key_configured(self):
        self._param("api_keys", "")
        result, _ = self._invoke(headers={"X-API-Key": self.KEY})
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "NO_API_KEY_CONFIGURED")

    def test_ip_still_enforced(self):
        result, _ = self._invoke(headers={"X-API-Key": self.KEY}, remote="8.8.8.8")
        self.assertEqual(result[0], 403)
        self.assertEqual(result[1]["error_code"], "IP_NOT_ALLOWED")

    def test_bad_auth_mode_rejected(self):
        self._param("auth_mode", "magic")
        result, _ = self._invoke(headers={"X-API-Key": self.KEY})
        self.assertEqual(result[0], 401)
        self.assertEqual(result[1]["error_code"], "BAD_AUTH_MODE")

    def test_oversize_body_rejected_before_auth(self):
        self._param("max_body_bytes", "10")
        result, _ = self._invoke(headers={"X-API-Key": self.KEY}, body=b"x" * 64)
        self.assertEqual(result[0], 413)
        self.assertEqual(result[1]["error_code"], "PAYLOAD_TOO_LARGE")

    def test_body_limit_unset_allows_any_size(self):
        result, _ = self._invoke(headers={"X-API-Key": self.KEY}, body=b"x" * 100_000)
        self.assertEqual(result, ("ok", {"ok": True}))
