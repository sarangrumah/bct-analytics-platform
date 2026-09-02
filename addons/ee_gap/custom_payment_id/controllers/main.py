# -*- coding: utf-8 -*-
"""Webhook endpoints for Indonesia payment gateways.

Three endpoints, one per gateway:

- ``POST /custom_payment_id/webhook/midtrans`` — Midtrans HTTP
  Notification. Body is JSON with ``order_id``, ``status_code``,
  ``gross_amount``, ``transaction_status``, ``signature_key``. We
  verify ``signature_key == SHA512(order_id+status_code+gross_amount+server_key)``.
- ``POST /custom_payment_id/webhook/xendit`` — Xendit invoice/charge
  callback. Body is JSON with ``external_id`` + ``status``. We verify
  the ``x-callback-token`` HTTP header equals the merchant's verification
  token (stored in ``x_id_webhook_secret``).
- ``POST /custom_payment_id/webhook/doku`` — DOKU notification. Body
  is JSON; we verify the ``Signature`` header using the configured
  secret and DOKU's HMAC-SHA256 scheme.

All endpoints respond ``200`` on accepted (or already-acked) payloads
so the gateway stops retrying; ``400`` on verification failure.
"""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.http import request

from ..models.adapter_midtrans import MidtransAdapter
from ..models.adapter_xendit import XenditAdapter
from ..models.adapter_doku import DokuAdapter
from ..models.adapter_pos import PosAdapter

_logger = logging.getLogger(__name__)


# Midtrans status_code → Odoo payment.transaction state
_MIDTRANS_STATE_MAP = {
    "settlement": "done",
    "capture": "done",
    "pending": "pending",
    "deny": "cancel",
    "cancel": "cancel",
    "expire": "cancel",
    "failure": "error",
    "refund": "cancel",
    "partial_refund": "done",
    "chargeback": "error",
}

# Xendit invoice/charge status → Odoo state
_XENDIT_STATE_MAP = {
    "PAID": "done",
    "SETTLED": "done",
    "PENDING": "pending",
    "EXPIRED": "cancel",
    "FAILED": "error",
    "COMPLETED": "done",
    "SUCCEEDED": "done",
}

# DOKU notification status → Odoo state
_DOKU_STATE_MAP = {
    "SUCCESS": "done",
    "SETTLEMENT": "done",
    "PENDING": "pending",
    "FAILED": "error",
    "EXPIRED": "cancel",
    "CANCELED": "cancel",
}

# POS notification status → Odoo state (placeholder mapping)
_POS_STATE_MAP = {
    "PAID": "done",
    "SETTLED": "done",
    "SUCCESS": "done",
    "PENDING": "pending",
    "EXPIRED": "cancel",
    "CANCELLED": "cancel",
    "CANCELED": "cancel",
    "FAILED": "error",
}


def _json_body() -> dict:
    raw = request.httprequest.get_data() or b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _find_transaction(reference: str):
    if not reference:
        return request.env["payment.transaction"]
    return request.env["payment.transaction"].sudo().search([("reference", "=", reference)], limit=1)


def _reconcile_transaction(tx, new_state: str, raw_payload: dict) -> bool:
    """Transition ``tx`` to ``new_state`` and persist raw payload.

    Returns ``True`` on a successful state change, ``False`` when the
    transition was already final or no-op.
    """
    if not tx:
        return False
    tx.sudo().write({"x_id_raw_response": json.dumps(raw_payload, default=str)[:65000]})
    if tx.state in ("done", "cancel", "error") and tx.state == new_state:
        return False
    sudo_tx = tx.sudo()
    try:
        if new_state == "done":
            sudo_tx._set_done()
        elif new_state == "pending":
            sudo_tx._set_pending()
        elif new_state == "cancel":
            sudo_tx._set_canceled()
        elif new_state == "error":
            sudo_tx._set_error("Gateway reported error.")
        else:
            return False
    except Exception as e:  # noqa: BLE001 — surface in log, don't 500
        _logger.exception("Webhook reconcile failed for %s: %s", tx.reference, e)
        # payment.transaction does not inherit mail.thread in this build —
        # guard message_post so the audit note never turns a hook into a 500.
        if hasattr(sudo_tx, "message_post"):
            sudo_tx.message_post(body=f"Webhook reconcile error: {e}")
        return False
    if hasattr(sudo_tx, "message_post"):
        sudo_tx.message_post(
            body=(
                f"Gateway webhook reconciled state → <b>{new_state}</b>.<br/>"
                f"<pre>{json.dumps(raw_payload, indent=2, default=str)[:2000]}</pre>"
            )
        )
    return True


class IdPaymentWebhookController(http.Controller):
    # ---------------- Midtrans ----------------

    @http.route(
        "/custom_payment_id/webhook/midtrans",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def midtrans_webhook(self, **_kw):
        body = _json_body()
        order_id = (body.get("order_id") or "").strip()
        status_code = str(body.get("status_code") or "")
        gross_amount = str(body.get("gross_amount") or "")
        signature_key = (body.get("signature_key") or "").strip()
        tx_status = (body.get("transaction_status") or "").strip().lower()
        fraud = (body.get("fraud_status") or "").strip().lower()

        tx = _find_transaction(order_id)
        if not tx:
            _logger.warning("Midtrans webhook: unknown order_id=%s", order_id)
            return request.make_response("not found", status=404)

        provider = tx.provider_id.sudo()
        if provider.code != "midtrans":
            return request.make_response("provider mismatch", status=400)

        server_key = provider.x_id_server_key or ""
        if not MidtransAdapter.verify_notification_signature(
            order_id=order_id,
            status_code=status_code,
            gross_amount=gross_amount,
            server_key=server_key,
            signature_key=signature_key,
        ):
            _logger.warning("Midtrans webhook: signature mismatch for %s", order_id)
            return request.make_response("bad signature", status=400)

        # capture + accept fraud rules → done; capture + challenge → pending
        if tx_status == "capture" and fraud == "challenge":
            new_state = "pending"
        else:
            new_state = _MIDTRANS_STATE_MAP.get(tx_status, "pending")
        _reconcile_transaction(tx, new_state, body)
        return request.make_response("ok", status=200)

    # ---------------- Xendit ----------------

    @http.route(
        "/custom_payment_id/webhook/xendit",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def xendit_webhook(self, **_kw):
        body = _json_body()
        external_id = (body.get("external_id") or "").strip()
        status = (body.get("status") or "").strip().upper()

        tx = _find_transaction(external_id)
        if not tx:
            _logger.warning("Xendit webhook: unknown external_id=%s", external_id)
            return request.make_response("not found", status=404)

        provider = tx.provider_id.sudo()
        if provider.code != "xendit":
            return request.make_response("provider mismatch", status=400)

        provided = request.httprequest.headers.get("x-callback-token", "")
        expected = provider.x_id_webhook_secret or ""
        if not XenditAdapter.verify_callback_token(provided, expected):
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure - logs external_id, never the token value
            _logger.warning("Xendit webhook: callback token mismatch for %s", external_id)
            return request.make_response("bad token", status=400)

        new_state = _XENDIT_STATE_MAP.get(status, "pending")
        _reconcile_transaction(tx, new_state, body)
        return request.make_response("ok", status=200)

    # ---------------- DOKU ----------------

    @http.route(
        "/custom_payment_id/webhook/doku",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def doku_webhook(self, **_kw):
        raw_bytes = request.httprequest.get_data() or b""
        body = _json_body()
        # DOKU shape: {"order": {"invoice_number": "..."}, "transaction": {"status": "SUCCESS"}}
        invoice_number = ((body.get("order") or {}).get("invoice_number") or body.get("invoice_number") or "").strip()
        status = ((body.get("transaction") or {}).get("status") or body.get("status") or "").strip().upper()

        tx = _find_transaction(invoice_number)
        if not tx:
            _logger.warning("DOKU webhook: unknown invoice_number=%s", invoice_number)
            return request.make_response("not found", status=404)

        provider = tx.provider_id.sudo()
        if provider.code != "doku":
            return request.make_response("provider mismatch", status=400)

        headers = request.httprequest.headers
        ok = DokuAdapter.verify_notification_signature(
            client_id=headers.get("Client-Id", ""),
            request_id=headers.get("Request-Id", ""),
            timestamp=headers.get("Request-Timestamp", ""),
            path="/custom_payment_id/webhook/doku",
            body=raw_bytes,
            secret=provider.x_id_server_key or "",
            provided_signature=headers.get("Signature", ""),
        )
        if not ok:
            _logger.warning("DOKU webhook: signature mismatch for %s", invoice_number)
            return request.make_response("bad signature", status=400)

        new_state = _DOKU_STATE_MAP.get(status, "pending")
        _reconcile_transaction(tx, new_state, body)
        return request.make_response("ok", status=200)

    # ---------------- POS ----------------

    @http.route(
        "/custom_payment_id/webhook/pos",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def pos_webhook(self, **_kw):
        raw_bytes = request.httprequest.get_data() or b""
        body = _json_body()
        # POS shape (placeholder): {"order_id": "...", "status": "PAID"}
        reference = (body.get("order_id") or body.get("reference") or "").strip()
        status = (body.get("status") or "").strip().upper()

        tx = _find_transaction(reference)
        if not tx:
            _logger.warning("POS webhook: unknown order_id=%s", reference)
            return request.make_response("not found", status=404)

        provider = tx.provider_id.sudo()
        if provider.code != "pos":
            return request.make_response("provider mismatch", status=400)

        provided = request.httprequest.headers.get("X-POS-Signature", "")
        if not PosAdapter.verify_notification_signature(
            raw_body=raw_bytes,
            provided_signature=provided,
            webhook_secret=provider.x_id_webhook_secret or "",
        ):
            _logger.warning("POS webhook: signature mismatch for %s", reference)
            return request.make_response("bad signature", status=400)

        new_state = _POS_STATE_MAP.get(status, "pending")
        _reconcile_transaction(tx, new_state, body)
        return request.make_response("ok", status=200)
