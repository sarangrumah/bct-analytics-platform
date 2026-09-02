# -*- coding: utf-8 -*-
"""Public affiliate click-tracking endpoint.

CORS-enabled GET so a headless storefront can register a click when a visitor
lands via ``?aff=CODE``. The storefront owns the first-party ``aff_ref`` cookie
(consent-gated); this endpoint only persists analytics + validates the code.
"""

from __future__ import annotations

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _cors_origin():
    return (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param(
            "custom_affiliate.cors_origin",
            request.env["ir.config_parameter"].sudo().get_param("custom_storefront_api.cors_origin", "*"),
        )
        or "*"
    )


def _resp(payload, status=200):
    resp = request.make_json_response(payload, status=status)
    origin = _cors_origin()
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Vary"] = "Origin"
    if origin != "*":
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


class AffiliateController(http.Controller):
    @http.route(
        "/affiliate/track",
        type="http",
        auth="public",
        methods=["GET", "OPTIONS"],
        csrf=False,
        save_session=False,
    )
    def track(self, code=None, landing=None, ref=None, session=None, link=None, **kw):
        if request.httprequest.method == "OPTIONS":
            return _resp({}, status=204)
        affiliate = request.env["custom.affiliate"].sudo()._resolve_active(code)
        if not affiliate:
            # Unknown/inactive identifiers are ignored without error (spec).
            return _resp({"ok": True, "valid": False})
        link_rec = request.env["custom.affiliate.link"].browse()
        if link:
            link_rec = (
                request.env["custom.affiliate.link"]
                .sudo()
                .search([("short_code", "=", link), ("affiliate_id", "=", affiliate.id)], limit=1)
            )
        httpreq = request.httprequest
        ip = (httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or "").split(",")[0].strip()
        request.env["custom.affiliate.click"].sudo()._record_click(
            affiliate,
            link=link_rec or None,
            landing_url=landing,
            referrer=ref or httpreq.headers.get("Referer"),
            session_key=session,
            ip=ip,
            user_agent=httpreq.headers.get("User-Agent"),
        )
        return _resp({"ok": True, "valid": True, "code": affiliate.affiliate_code})
