# -*- coding: utf-8 -*-
"""Small JSON/HMAC helpers shared by the VAS PMO controllers."""

import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

REPLAY_WINDOW_SECONDS = 300
PARAM_SECRET = "custom_core.secure_endpoint.pmo.secret"
PARAM_CORS = "custom_project_api.cors_origin"


def _cors_headers():
    origin = request.env["ir.config_parameter"].sudo().get_param(PARAM_CORS) or ""
    headers = [("Content-Type", "application/json; charset=utf-8")]
    if origin:
        headers += [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Signature, X-Timestamp"),
            ("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS"),
            ("Access-Control-Allow-Credentials", "true"),
        ]
    return headers


def ok(payload, status=200):
    return request.make_response(
        json.dumps({"ok": True, "data": payload}, default=str),
        headers=_cors_headers(),
        status=status,
    )


def err(code, message, status=400):
    return request.make_response(
        json.dumps({"ok": False, "error": {"code": code, "message": message}}),
        headers=_cors_headers(),
        status=status,
    )


def json_body():
    try:
        raw = request.httprequest.get_data(as_text=True) or "{}"
        body = json.loads(raw)
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def client_meta():
    env = request.httprequest.environ
    return env.get("HTTP_USER_AGENT", ""), env.get("REMOTE_ADDR", "")


def verify_hmac():
    """Return None when the signature is valid, otherwise a ready-made error response."""
    secret = request.env["ir.config_parameter"].sudo().get_param(PARAM_SECRET)
    if not secret:
        return err("NOT_CONFIGURED", "HMAC secret is not configured", status=503)

    signature = request.httprequest.headers.get("X-Signature") or ""
    timestamp = request.httprequest.headers.get("X-Timestamp") or ""
    if not signature or not timestamp:
        return err("MISSING_SIGNATURE", "X-Signature and X-Timestamp are required", status=401)

    try:
        skew = abs(int(time.time()) - int(timestamp))
    except ValueError:
        return err("BAD_TIMESTAMP", "X-Timestamp must be a unix timestamp", status=401)
    if skew > REPLAY_WINDOW_SECONDS:
        return err("STALE_TIMESTAMP", "Request is outside the replay window", status=401)

    raw = request.httprequest.get_data() or b""
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + raw,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return err("BAD_SIGNATURE", "Signature mismatch", status=401)
    return None


class PmoPreflight(http.Controller):
    """Browsers ask before they POST cross-origin."""

    @http.route(
        ["/pmo/api/<path:subpath>"],
        type="http",
        auth="public",
        methods=["OPTIONS"],
        csrf=False,
        save_session=False,
    )
    def preflight(self, subpath=None, **kw):
        return request.make_response("", headers=_cors_headers(), status=204)
