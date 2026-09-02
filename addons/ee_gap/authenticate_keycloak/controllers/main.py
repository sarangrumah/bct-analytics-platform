# -*- coding: utf-8 -*-
"""Authorization-code flow on top of stock ``auth_oauth``.

Two touch points only:

1. ``list_providers`` — stock hardcodes ``response_type=token``; providers on
   the authorization-code flow need ``response_type=code`` and our redirect URI.
2. ``/auth_keycloak/code`` — exchange the code for an access token, then hand
   over to the stock ``auth_oauth()`` + ``session.authenticate()`` path.

Everything after the token exchange (token validation, user lookup,
``hr.employee`` linking, HC API enrichment) is stock behaviour plus
``custom_hr_sso_keycloak``'s ``_auth_oauth_signin`` override, which stock
``auth_oauth()`` calls. Nothing of that is reimplemented here.
"""

from __future__ import annotations

import json
import logging

import requests
import werkzeug.urls
from werkzeug.exceptions import BadRequest

from odoo import SUPERUSER_ID, http
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.addons.auth_oauth.controllers.main import OAuthController, OAuthLogin
from odoo.addons.web.controllers.utils import _get_login_redirect_url, ensure_db

_logger = logging.getLogger(__name__)

REDIRECT_PATH = "/auth_keycloak/code"
TOKEN_TIMEOUT = 10


class KeycloakOAuthLogin(OAuthLogin):
    def list_providers(self):
        providers = super().list_providers()
        if not providers:
            return providers

        code_flow_ids = {
            p["id"]
            for p in request.env["auth.oauth.provider"]
            .sudo()
            .search_read([("flow", "=", "authorization_code")], ["id"])
        }
        if not code_flow_ids:
            return providers

        redirect_uri = request.httprequest.url_root.rstrip("/") + REDIRECT_PATH
        for provider in providers:
            if provider["id"] not in code_flow_ids:
                continue
            # Stock built auth_link with response_type=token. Rebuild it for the
            # code flow, reusing stock's state (it already carries db, provider
            # id and the post-login redirect).
            params = dict(
                response_type="code",
                client_id=provider["client_id"],
                redirect_uri=redirect_uri,
                scope=provider["scope"],
                state=json.dumps(self.get_state(provider)),
            )
            provider["auth_link"] = "%s?%s" % (
                provider["auth_endpoint"],
                werkzeug.urls.url_encode(params),
            )
        return providers


class KeycloakOAuthController(OAuthController):
    # auth='none' routes default to readonly=True in Odoo 19; signing in writes,
    # so this must mirror stock /auth_oauth/signin and opt out.
    @http.route(REDIRECT_PATH, type="http", auth="none", readonly=False)
    def keycloak_code_callback(self, **kw):
        state = json.loads(kw.get("state") or "{}")
        dbname = state.get("d")
        provider_id = state.get("p")
        if not dbname or not provider_id or not http.db_filter([dbname]):
            return BadRequest()
        ensure_db(db=dbname)

        if kw.get("error"):
            _logger.warning("keycloak: IdP returned error=%s", kw["error"])
            return request.redirect("/web/login?oauth_error=2")
        if not kw.get("code"):
            return request.redirect("/web/login?oauth_error=2")

        try:
            access_token = self._exchange_code(provider_id, kw["code"])
            # Stock path: validates the token against the provider's userinfo
            # endpoint, then _auth_oauth_signin resolves the user and raises
            # AccessDenied when it cannot.
            _, login, key = (
                request.env["res.users"]
                .with_user(SUPERUSER_ID)
                .auth_oauth(provider_id, {"access_token": access_token, "state": kw.get("state")})
            )
            credential = {"login": login, "token": key, "type": "oauth_token"}
            auth_info = request.session.authenticate(request.env, credential)
        except AccessDenied:
            _logger.info("keycloak: access denied (provider %s)", provider_id)
            return request.redirect("/web/login?oauth_error=3")
        except Exception:
            _logger.exception("keycloak: authorization-code sign-in failed")
            return request.redirect("/web/login?oauth_error=2")

        url = werkzeug.urls.url_unquote_plus(state.get("r") or "") or "/odoo"
        return request.redirect(_get_login_redirect_url(auth_info["uid"], url), 303)

    def _exchange_code(self, provider_id, code: str) -> str:
        provider = request.env["auth.oauth.provider"].sudo().browse(int(provider_id))
        if not provider.exists() or not provider.enabled:
            raise AccessDenied()
        if provider.flow != "authorization_code" or not provider.token_endpoint:
            raise AccessDenied()

        response = requests.post(
            provider.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": request.httprequest.url_root.rstrip("/") + REDIRECT_PATH,
                "client_id": provider.client_id,
                "client_secret": provider._get_client_secret(),
            },
            timeout=TOKEN_TIMEOUT,
        )
        if response.status_code != 200:
            # Never log the body — an IdP error can echo the request back.
            # Only the provider id and HTTP status reach the log, never the
            # client_secret or the token; the rule matches on the surrounding
            # token-exchange context, not on a real disclosure.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            _logger.warning(
                "keycloak: token exchange failed (provider %s, HTTP %s)",
                provider_id,
                response.status_code,
            )
            raise AccessDenied()
        access_token = response.json().get("access_token")
        if not access_token:
            raise AccessDenied()
        return access_token
