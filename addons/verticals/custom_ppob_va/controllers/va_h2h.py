# -*- coding: utf-8 -*-
"""Host-to-Host callback controllers for bank Virtual Accounts.

Two endpoints per bank:
  * inquiry: bank asks "does this VA exist, who is it".
  * payment: bank notifies "the customer has paid, please book it".

Both are authenticated per bank with HMAC-SHA256 over ``timestamp || body``,
the shared secret resolved from ``custom.ppob.va.bank.connection.credential_ref``
(an ir.config_parameter key). Replay is guarded by the platform's Redis-backed
``_NonceStore`` (falls back to per-worker memory); the money-movement idempotency
guarantee is the DB ``UNIQUE(bank_ref)`` on ``custom.ppob.va.topup`` -- a
duplicate callback credits the wallet once and returns the original ack.

Behind a reverse proxy the client IP is the first X-Forwarded-For hop.
"""

import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

# Reuse the platform secure-endpoint primitives (Redis nonce + IP allowlist).
from odoo.addons.custom_core.controllers.secure_endpoint import (
    _NonceStore,
    _check_ip_whitelist,
)

_logger = logging.getLogger(__name__)


def _client_ip():
    httpreq = request.httprequest
    remote = httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or ""
    return remote.split(",")[0].strip()


def _verify_va_signature(connection, bank_code, body, signature, timestamp):
    """HMAC verify with per-bank secret + skew + Redis-backed replay guard."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        _logger.warning("VA verify: bad timestamp %r", timestamp)
        return False
    skew = connection.max_clock_skew_s or 300
    if abs(time.time() - ts) > skew:
        _logger.warning("VA verify: skew exceeded (ts=%s now=%s skew=%s)", ts, int(time.time()), skew)
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
        _logger.warning("VA verify: empty secret for credential_ref=%r", connection.credential_ref)
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        _logger.warning("VA verify: hmac mismatch (bodylen=%s, sig_present=%s)", len(body), bool(signature))
        return False
    # Cross-worker best-effort replay throttle; the DB UNIQUE(bank_ref) is the
    # authoritative idempotency line for the actual money movement.
    if _NonceStore.seen(f"va:{bank_code}:{ts}:{signature}"):
        _logger.warning("VA verify: nonce replay va:%s:%s:%s..", bank_code, ts, signature[:8])
        return False
    return True


class VaH2HController(http.Controller):
    def _authenticate(self, bank_code):
        connection = (
            request.env["custom.ppob.va.bank.connection"]
            .sudo()
            .search(
                [("bank_code", "=", bank_code), ("status", "=", "active")],
                limit=1,
            )
        )
        if not connection:
            return None, {"ok": False, "error_code": "NOT_CONFIGURED"}
        remote = _client_ip()
        if not _check_ip_whitelist(connection.ip_whitelist, remote):
            _logger.warning("VA %s callback rejected: IP %s not whitelisted", bank_code, remote)
            return None, {"ok": False, "error_code": "IP_NOT_ALLOWED"}
        body = request.httprequest.get_data() or b""
        signature = request.httprequest.headers.get("X-Signature")
        timestamp = request.httprequest.headers.get("X-Timestamp")
        if not _verify_va_signature(connection, bank_code, body, signature, timestamp):
            _logger.warning("VA %s callback rejected: bad signature", bank_code)
            return None, {"ok": False, "error_code": "BAD_SIGNATURE"}
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return None, {"ok": False, "error_code": "BAD_JSON"}
        return payload, None

    @http.route(
        "/api/ppob/va/<string:bank_code>/inquiry",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        readonly=False,
    )
    def inquiry(self, bank_code, **_):
        bank_code = (bank_code or "").upper()
        payload, err = self._authenticate(bank_code)
        if err:
            return request.make_json_response(err, status=401)
        va_number = payload.get("va_number") or payload.get("virtualAccount")
        va = request.env["custom.ppob.va.account"].sudo()._find_by_va_number(bank_code, va_number)
        if not va:
            return request.make_json_response(
                {"ok": False, "error_code": "VA_NOT_FOUND"},
                status=404,
            )
        return request.make_json_response(
            {
                "ok": True,
                "va_number": va.va_number,
                "customer_name": va.mitra_id.display_name,
                "mitra_code": va.mitra_id.x_custom_ppob_mitra_code,
                "wallet_class": va.wallet_id.class_id.code,
            }
        )

    @http.route(
        "/api/ppob/va/<string:bank_code>/payment",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        readonly=False,
    )
    def payment(self, bank_code, **_):
        bank_code = (bank_code or "").upper()
        payload, err = self._authenticate(bank_code)
        if err:
            return request.make_json_response(err, status=401)
        va_number = payload.get("va_number") or payload.get("virtualAccount")
        amount = float(payload.get("amount") or 0.0)
        bank_ref = payload.get("bank_ref") or payload.get("trxId")
        if not (va_number and amount > 0 and bank_ref):
            return request.make_json_response(
                {"ok": False, "error_code": "BAD_PAYLOAD"},
                status=400,
            )
        Topup = request.env["custom.ppob.va.topup"].sudo()
        existing = Topup.search([("bank_ref", "=", bank_ref)], limit=1)
        if existing:
            # Idempotent: duplicate callback -> credit already done, return the ack.
            return request.make_json_response(
                {
                    "ok": True,
                    "duplicate": True,
                    "topup_ref": existing.name,
                }
            )
        va = request.env["custom.ppob.va.account"].sudo()._find_by_va_number(bank_code, va_number)
        if not va:
            return request.make_json_response(
                {"ok": False, "error_code": "VA_NOT_FOUND"},
                status=404,
            )
        # auth='none' requests have no env.company, so company-dependent
        # defaults (currency) would resolve empty -> set them from the wallet.
        topup = Topup.with_company(va.wallet_id.company_id).create(
            {
                "va_account_id": va.id,
                "amount": amount,
                "bank_ref": bank_ref,
                "source": "h2h_callback",
                "state": "settled",
                "currency_id": va.wallet_id.currency_id.id,
            }
        )
        topup.action_credit_wallet()
        return request.make_json_response(
            {
                "ok": True,
                "topup_ref": topup.name,
                "new_balance": va.wallet_id.balance,
            }
        )
