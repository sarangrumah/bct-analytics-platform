# -*- coding: utf-8 -*-
"""CORS helpers + a catch-all OPTIONS preflight handler.

The allowed origin is read from ``ir.config_parameter`` key
``custom_storefront_api.cors_origin`` so each tenant configures its own
storefront origin. Credentials are only allowed for a concrete origin
(never for the ``*`` wildcard — that combination is invalid per the
Fetch spec).
"""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_DEFAULT_ORIGIN = "*"


def _origin() -> str:
    return (
        request.env["ir.config_parameter"].sudo().get_param("custom_storefront_api.cors_origin", _DEFAULT_ORIGIN)
        or _DEFAULT_ORIGIN
    )


def cors_headers() -> list:
    origin = _origin()
    headers = [
        ("Access-Control-Allow-Origin", origin),
        ("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"),
        (
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Cart-Token, X-Requested-With",
        ),
        ("Access-Control-Max-Age", "600"),
        ("Vary", "Origin"),
    ]
    if origin != "*":
        headers.append(("Access-Control-Allow-Credentials", "true"))
    return headers


def json_response(payload: dict, status: int = 200):
    resp = request.make_json_response(payload, status=status)
    for key, value in cors_headers():
        resp.headers[key] = value
    return resp


def ok(data=None, status: int = 200, **extra):
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return json_response(body, status=status)


def err(error_code: str, message: str = "", status: int = 400):
    return json_response({"ok": False, "error_code": error_code, "message": message}, status=status)


def json_body() -> dict:
    raw = request.httprequest.get_data() or b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def client_meta() -> tuple:
    httpreq = request.httprequest
    ip = (httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or "").split(",")[0].strip()
    return httpreq.headers.get("User-Agent", ""), ip


class StorefrontCorsController(http.Controller):
    """Single OPTIONS preflight handler for every storefront API path."""

    @http.route(
        ["/storefront/api/<path:rest>"],
        type="http",
        auth="public",
        methods=["OPTIONS"],
        csrf=False,
        save_session=False,
    )
    def preflight(self, rest=None, **kw):
        resp = request.make_response("", headers=cors_headers())
        resp.status_code = 204
        return resp
