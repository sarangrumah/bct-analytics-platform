# -*- coding: utf-8 -*-
"""Token lifecycle: refresh-before-expiry, login fallback, static key mode."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from ..models.esb_session import SKEW_S
from .common import EsbTestCase, load_fixture


@tagged("post_install", "-at_install", "esb")
class TestEsbSession(EsbTestCase):
    def _refresh_fixture(self):
        payload = load_fixture("login_ok")
        payload["result"] = dict(payload["result"], accessToken="access-token-2", refreshToken="refresh-token-2")
        return payload

    def test_first_call_logs_in_and_stores_both_tokens(self):
        self.transport.register("POST", "/auth/login", load_fixture("login_ok"))

        token = self.session._ensure_token()

        self.assertEqual(token, "access-token-1")
        self.assertEqual(self.session.refresh_token, "refresh-token-1")
        self.assertEqual(self.session.company_code, "QA1")
        self.assertEqual(self.session.login_count, 1)
        self.assertFalse(self.session.last_error)

    def test_valid_token_is_reused_without_another_login(self):
        self.given_logged_in()

        self.session._ensure_token()
        self.session._ensure_token()

        self.assertEqual(self.transport.count("POST", "/auth/login"), 1, "a live token must not trigger a login")

    def test_refresh_fires_before_expiry_not_after(self):
        """Waiting for a 401 would leak failed calls; we refresh SKEW_S early."""
        self.given_logged_in()
        self.transport.register("GET", "/auth/refresh", self._refresh_fixture())
        # Token still technically valid, but inside the skew window.
        self.session.access_expires_at = fields.Datetime.now() + timedelta(seconds=SKEW_S - 60)

        token = self.session._ensure_token()

        self.assertEqual(token, "access-token-2")
        self.assertEqual(self.transport.count("GET", "/auth/refresh"), 1)
        self.assertEqual(self.transport.count("POST", "/auth/login"), 1, "refresh must be preferred over a re-login")

    def test_dead_refresh_token_falls_back_to_login(self):
        self.given_logged_in()
        self.session.write(
            {
                "access_expires_at": fields.Datetime.now() - timedelta(minutes=1),
                "refresh_expires_at": fields.Datetime.now() - timedelta(minutes=1),
            }
        )

        self.session._ensure_token()

        self.assertEqual(self.transport.count("POST", "/auth/login"), 2)
        self.assertEqual(self.transport.count("GET", "/auth/refresh"), 0, "an expired refresh token is not attempted")

    def test_rejected_refresh_falls_back_to_login(self):
        self.given_logged_in()
        self.transport.register("GET", "/auth/refresh", load_fixture("unauthorized"))
        self.session.access_expires_at = fields.Datetime.now() - timedelta(minutes=1)

        token = self.session._ensure_token()

        self.assertEqual(token, "access-token-1")
        self.assertEqual(self.transport.count("POST", "/auth/login"), 2)

    def test_bad_credentials_record_the_error_and_return_empty(self):
        self.transport.register("POST", "/auth/login", load_fixture("login_bad_credentials"))

        token = self.session._ensure_token()

        self.assertFalse(token)
        self.assertIn("EC03100032", self.session.last_error)

    def test_missing_password_parameter_is_reported_clearly(self):
        self.param.set_param("custom_esb_connector.esb_password", "")

        token = self.session._ensure_token()

        self.assertFalse(token)
        self.assertIn("esb_password", self.session.last_error)

    def test_static_api_key_mode_never_logs_in(self):
        """The PIC can issue a static key, which sidesteps session eviction entirely."""
        self.session.write({"auth_mode": "static", "credential_ref": "custom_esb_connector.esb_password"})

        token = self.session._ensure_token()

        self.assertEqual(token, "s3cr3t")
        self.assertEqual(self.transport.count("POST", "/auth/login"), 0)

    def test_invalidate_token_forces_reauthentication(self):
        self.given_logged_in()

        self.session._invalidate_token()
        self.session._ensure_token()

        self.assertEqual(self.transport.count("POST", "/auth/login"), 2)
        self.assertTrue(self.session.access_token)

    def test_secret_is_read_from_config_parameter_not_stored(self):
        """The password must never be persisted on the session record."""
        self.given_logged_in()

        stored = self.session.read(["username", "credential_ref"])[0]

        self.assertEqual(stored["credential_ref"], "custom_esb_connector.esb_password")
        self.assertNotIn("s3cr3t", str(stored.values()))

    def test_corev1_host_shares_the_core_session(self):
        """One set of ESB credentials serves all three hosts."""
        self.given_logged_in()

        resolved = self.env["custom.esb.session"]._for_config(self.corev1_config)

        self.assertEqual(resolved, self.session)
