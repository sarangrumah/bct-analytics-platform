# -*- coding: utf-8 -*-
"""Two inbound HTTP ingest endpoints for the POS mirror bridge.

  * POST /api/ppob/pos/pos  -- sales / top-up / refund + mitra balance.
  * POST /api/ppob/pos/h2h  -- biller fulfillment + cost + deposit.

Both are authenticated with HMAC-SHA256 over ``timestamp || body`` (per-feed
secret from ``custom.ppob.pos.connection.credential_ref``), IP allowlist +
Redis-backed nonce replay guard (reused from ``custom_core``). Money-projection
idempotency is the DB ``UNIQUE(pos_ref)`` / ``UNIQUE(h2h_ref)`` on the join.

Behind a reverse proxy the client IP is the first X-Forwarded-For hop.
"""

import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import (
    _NonceStore,
    _check_ip_whitelist,
)

_logger = logging.getLogger(__name__)


def _client_ip():
    httpreq = request.httprequest
    remote = httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or ""
    return remote.split(",")[0].strip()


def _verify_signature(connection, feed, body, signature, timestamp):
    """HMAC-SHA256 verify with per-feed secret + skew + replay guard."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    skew = connection.max_clock_skew_s or 300
    if abs(time.time() - ts) > skew:
        return False
    secret = (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param(
            connection.credential_ref or "",
            "",
        )
    )
    if not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        return False
    if _NonceStore.seen(f"pos:{feed}:{ts}:{signature}"):
        return False
    return True


class PosIngestController(http.Controller):
    def _authenticate(self, feed):
        connection = (
            request.env["custom.ppob.pos.connection"]
            .sudo()
            .search(
                [("feed", "=", feed), ("status", "=", "active")],
                limit=1,
            )
        )
        if not connection:
            return None, {"ok": False, "error_code": "NOT_CONFIGURED"}, 401
        remote = _client_ip()
        if not _check_ip_whitelist(connection.ip_whitelist, remote):
            _logger.warning("POS %s feed rejected: IP %s not whitelisted", feed, remote)
            return None, {"ok": False, "error_code": "IP_NOT_ALLOWED"}, 403
        body = request.httprequest.get_data() or b""
        signature = request.httprequest.headers.get("X-Signature")
        timestamp = request.httprequest.headers.get("X-Timestamp")
        if not _verify_signature(connection, feed, body, signature, timestamp):
            _logger.warning("POS %s feed rejected: bad signature", feed)
            return None, {"ok": False, "error_code": "BAD_SIGNATURE"}, 401
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, {"ok": False, "error_code": "BAD_JSON"}, 400
        return payload, None, 200

    def _handle(self, feed):
        payload, err, status = self._authenticate(feed)
        if err:
            return request.make_json_response(err, status=status)
        if not payload.get("pos_trx_ref"):
            return request.make_json_response({"ok": False, "error_code": "MISSING_POS_TRX_REF"}, status=400)
        # auth="public" requests have no company context, so company-dependent
        # defaults (currency, wallet/journal company) would not resolve; bind an
        # explicit company before touching the ORM.
        company = request.env.company or request.env["res.company"].sudo().search([], order="id", limit=1)
        Join = request.env["custom.ppob.pos.txn"].sudo().with_company(company)
        join = Join._ingest_event(feed, payload)
        if not join:
            # Routed to the skipped queue (unmapped / non-terminal / post error).
            return request.make_json_response(
                {"ok": True, "accepted": True, "posted": False, "note": "queued for review"}, status=202
            )
        return request.make_json_response(
            {
                "ok": True,
                "accepted": True,
                "posted": bool(join.pos_posted if feed == "pos" else join.h2h_posted),
                "join_ref": join.name,
                "match_state": join.match_state,
            }
        )

    @http.route("/api/ppob/pos/pos", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def pos_feed(self, **_):
        return self._handle("pos")

    @http.route("/api/ppob/pos/h2h", type="http", auth="public", readonly=False, methods=["POST"], csrf=False)
    def h2h_feed(self, **_):
        return self._handle("h2h")
