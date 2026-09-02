# -*- coding: utf-8 -*-
"""Login, refresh, logout, me."""

import logging

from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request

from .http_helpers import client_meta, err, json_body, ok

_logger = logging.getLogger(__name__)


def _validator():
    return request.env["auth.jwt.validator"].sudo()._get_validator_by_name("pmo")


def _access_ttl():
    return int(
        request.env["ir.config_parameter"]
        .sudo()
        .get_param(
            "custom_project_api.access_ttl",
            "900",
        )
    )


def _serialize_user(user):
    groups = {
        "admin": user.has_group("custom_project_portfolio.group_pmo_admin"),
        "lead": user.has_group("custom_project_portfolio.group_pmo_lead"),
        "po": user.has_group("custom_project_portfolio.group_pmo_po"),
        "ba": user.has_group("custom_project_portfolio.group_pmo_ba"),
        "brand_pic": user.has_group("custom_project_portfolio.group_pmo_vertical_pic"),
    }
    verticals = (
        request.env["custom.project.vertical"]
        .sudo()
        .search(
            [
                "|",
                ("ba_ids", "in", user.id),
                ("vertical_po_id", "=", user.id),
            ]
        )
    )
    return {
        "id": user.id,
        "login": user.login,
        "name": user.name,
        "email": user.email or "",
        "roles": [key for key, value in groups.items() if value],
        "verticals": [{"id": v.id, "code": v.code, "name": v.name} for v in verticals],
    }


def _issue_session(user):
    validator = _validator()
    ttl = _access_ttl()
    access = validator._encode(
        payload={"sub": user.login, "email": user.email or "", "name": user.name},
        secret=validator.secret_key,
        expire=ttl,
    )
    ua, ip = client_meta()
    refresh = request.env["custom.pmo.token"].sudo()._issue(user, ua, ip)
    return {
        "access": access,
        "refresh": refresh,
        "expires_in": ttl,
        "user": _serialize_user(user),
    }


def _password_ok(user, password):
    """Check a password without betting on one Odoo release's signature.

    ``_check_credentials`` took ``(password, env)`` up to Odoo 17 and takes
    ``(credential_dict, env)`` from 18 on. Both are tried; anything other than a clean
    AccessDenied is re-raised so a genuine bug never looks like a wrong password.
    """
    scoped = user.with_user(user)
    attempts = (
        lambda: scoped._check_credentials(
            {"type": "password", "password": password},
            {"interactive": False},
        ),
        lambda: scoped._check_credentials(password, {"interactive": False}),
    )
    last_type_error = None
    for attempt in attempts:
        try:
            attempt()
            return True
        except AccessDenied:
            return False
        except TypeError as exc:
            last_type_error = exc
            continue
    # The rule fires on the word "credentials" in the message. What is logged is the
    # TypeError from probing the method's arity -- Python's arity errors carry argument
    # counts, never argument values, so the password cannot reach the log here.
    # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
    _logger.error("VAS PMO: no usable _check_credentials signature: %s", last_type_error)
    raise last_type_error or TypeError("No usable _check_credentials signature")


class PmoAuth(http.Controller):
    @http.route(
        "/pmo/api/auth/login",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def login(self, **kw):
        body = json_body()
        login = (body.get("login") or "").strip()
        password = body.get("password") or ""
        if not login or not password:
            return err("MISSING_CREDENTIALS", "Login and password are required", status=400)

        user = (
            request.env["res.users"]
            .sudo()
            .search(
                [("login", "=", login), ("active", "=", True)],
                limit=1,
            )
        )
        if not user or not _password_ok(user, password):
            # Same message either way: a different answer for "no such user" hands an
            # attacker a list of valid logins.
            return err("INVALID_CREDENTIALS", "Wrong login or password", status=401)

        if not user.has_group("custom_project_portfolio.group_pmo_user") and not user.has_group(
            "custom_project_portfolio.group_pmo_vertical_pic"
        ):
            return err("NO_ACCESS", "This account has no VAS PMO access", status=403)

        return ok(_issue_session(user))

    @http.route(
        "/pmo/api/auth/refresh",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def refresh(self, **kw):
        body = json_body()
        tokens = request.env["custom.pmo.token"].sudo()
        row = tokens._resolve(body.get("refresh") or "")
        if not row:
            return err("INVALID_REFRESH", "Refresh token invalid or expired", status=401)
        ua, ip = client_meta()
        new_refresh = row._rotate(ua, ip)
        validator = _validator()
        ttl = _access_ttl()
        access = validator._encode(
            payload={
                "sub": row.user_id.login,
                "email": row.user_id.email or "",
                "name": row.user_id.name,
            },
            secret=validator.secret_key,
            expire=ttl,
        )
        return ok({"access": access, "refresh": new_refresh, "expires_in": ttl})

    @http.route(
        "/pmo/api/auth/logout",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def logout(self, **kw):
        body = json_body()
        row = request.env["custom.pmo.token"].sudo()._resolve(body.get("refresh") or "")
        if row:
            row.revoked = True
        return ok({"logged_out": True})

    @http.route(
        "/pmo/api/auth/me",
        type="http",
        auth="jwt_pmo",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def me(self, **kw):
        return ok(_serialize_user(request.env.user))
