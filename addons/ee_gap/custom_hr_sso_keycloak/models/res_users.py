# -*- coding: utf-8 -*-
"""Keycloak SSO sign-in glue.

Thin override of ``auth_oauth``'s ``_auth_oauth_signin``:

1. **Adopt-by-email** before ``super()`` — link the Keycloak identity (``sub`` ->
   ``oauth_uid``) onto an existing local user matched by ``login == email`` so a
   first-time SSO login reuses the account instead of creating a duplicate.
2. **No-user policy** — if there is no local account and JIT creation is off
   (default), block with ``AccessDenied``; the per-tenant
   ``custom_hr_sso_keycloak.jit_create`` parameter opts into standard signup.
3. **Non-blocking HR sync** — after authentication, delegate to ``hr.sso.sync``
   inside a ``try/except`` that only logs; a sync hiccup must never fail login.

All business logic lives in ``hr.sso.sync`` (testable / delegable); this stays thin.
"""

from __future__ import annotations

import logging

from odoo import _, api, models
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)

JIT_PARAM = "custom_hr_sso_keycloak.jit_create"


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _auth_oauth_signin(self, provider, validation, params):
        validation = validation or {}
        matched = self._hr_sso_adopt_existing_user(provider, validation)
        if matched is None and not self._hr_sso_jit_enabled():
            # Mirrors the legacy "email-not-registered" behaviour; surfaces as
            # /web/login?oauth_error=3 via the auth_oauth controller.
            raise AccessDenied(_("No Odoo account is linked to this SSO identity."))

        login = super()._auth_oauth_signin(provider, validation, params)

        try:
            self.env["hr.sso.sync"].sync_for_login(login, validation)
        except Exception as exc:  # never block login on a sync hiccup
            _logger.warning("HR SSO: employee sync failed for %s: %s", login, exc)
        return login

    # ------------------------------------------------------------------
    def _hr_sso_jit_enabled(self) -> bool:
        raw = self.env["ir.config_parameter"].sudo().get_param(JIT_PARAM, "0")
        return str(raw).strip().lower() in ("1", "true", "yes")

    def _hr_sso_adopt_existing_user(self, provider, validation):
        """Return the local user for this SSO identity, linking it on first login.

        - already linked by ``oauth_uid`` -> return it (normal path),
        - else matched by email -> stamp ``oauth_provider_id`` / ``oauth_uid`` and return it,
        - else -> ``None`` (caller decides block vs JIT).
        """
        oauth_uid = validation.get("user_id")  # auth_oauth maps OIDC `sub` -> user_id
        if not oauth_uid:
            return None

        Users = self.sudo()
        linked = Users.search(
            [("oauth_uid", "=", oauth_uid), ("oauth_provider_id", "=", provider)],
            limit=1,
        )
        if linked:
            return linked

        email = (validation.get("email") or validation.get("preferred_username") or "").strip()
        if not email:
            return None

        # `=ilike` without wildcards is a case-insensitive exact match.
        user = Users.search([("login", "=ilike", email)], limit=1)
        if not user:
            return None
        if not user.oauth_uid:
            user.write({"oauth_provider_id": provider, "oauth_uid": oauth_uid})
        return user
