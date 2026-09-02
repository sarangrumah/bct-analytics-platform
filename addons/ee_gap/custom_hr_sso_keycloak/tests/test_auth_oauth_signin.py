# -*- coding: utf-8 -*-
"""_auth_oauth_signin override: adopt-by-email, block-when-no-user, sync never blocks."""

from unittest.mock import patch

from odoo.exceptions import AccessDenied
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestAuthOAuthSignin(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Users = cls.env["res.users"]
        cls.provider = cls.env.ref("custom_hr_sso_keycloak.provider_keycloak_hr")
        cls.ICP = cls.env["ir.config_parameter"].sudo()

    def _make_user(self, login):
        return self.Users.create({"name": login, "login": login})

    def test_adopt_existing_user_by_email(self):
        """Existing local user is linked (oauth_uid set) and login returns."""
        user = self._make_user("alice@example.com")
        validation = {"user_id": "kc-alice", "email": "Alice@Example.com"}  # case-insensitive
        login = self.Users._auth_oauth_signin(self.provider.id, validation, {"access_token": "tok"})
        self.assertEqual(login, "alice@example.com")
        self.assertEqual(user.oauth_uid, "kc-alice")
        self.assertEqual(user.oauth_provider_id, self.provider)

    def test_block_when_no_user_and_jit_off(self):
        """No local account + JIT off (default) -> AccessDenied (blocked)."""
        self.ICP.set_param("custom_hr_sso_keycloak.jit_create", "0")
        with self.assertRaises(AccessDenied):
            self.Users._auth_oauth_signin(
                self.provider.id, {"user_id": "kc-ghost", "email": "ghost@example.com"}, {"access_token": "t"}
            )

    def test_login_not_blocked_when_sync_raises(self):
        """A failure inside hr.sso.sync must never break authentication."""
        self._make_user("bob@example.com")
        sync_model = type(self.env["hr.sso.sync"])
        with patch.object(sync_model, "sync_for_login", side_effect=Exception("boom")):
            login = self.Users._auth_oauth_signin(
                self.provider.id, {"user_id": "kc-bob", "email": "bob@example.com"}, {"access_token": "t"}
            )
        self.assertEqual(login, "bob@example.com")

    def test_jit_toggle_and_adopt_miss(self):
        """JIT helper reflects the param; adopt returns None for unknown email."""
        self.assertFalse(self.Users._hr_sso_jit_enabled())
        self.ICP.set_param("custom_hr_sso_keycloak.jit_create", "1")
        self.assertTrue(self.Users._hr_sso_jit_enabled())
        self.assertIsNone(
            self.Users._hr_sso_adopt_existing_user(self.provider.id, {"user_id": "kc-x", "email": "nobody@example.com"})
        )
