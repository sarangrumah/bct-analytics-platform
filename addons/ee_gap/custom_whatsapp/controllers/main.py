# -*- coding: utf-8 -*-
"""Meta WhatsApp Cloud API webhook endpoints.

Two methods on the same path::

    GET  /custom_whatsapp/webhook/<account_id>  — verify-token handshake
    POST /custom_whatsapp/webhook/<account_id>  — status + inbound message events

Both endpoints are public (``auth='public'``) because Meta cannot
authenticate against Odoo; the verify-token (GET) and the inbound
payload's own ``account_id`` segment + downstream lookups provide the
security boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


def _baileys_signature_valid(secret: str, raw_body: bytes, header: str) -> bool:
    """Constant-time HMAC-SHA256 check for ``X-Baileys-Signature: sha256=<hex>``."""
    if not secret or not header:
        return False
    if not header.lower().startswith("sha256="):
        return False
    sent = header.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(sent.lower(), expected.lower())
    except Exception:  # pragma: no cover — extremely defensive
        return False


class WhatsappWebhookController(http.Controller):
    @http.route(
        "/custom_whatsapp/webhook/<int:account_id>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def webhook_verify(self, account_id, **kwargs):
        """Meta verify-token handshake.

        Meta calls this once when the webhook URL is registered.
        Query params: ``hub.mode``, ``hub.verify_token``, ``hub.challenge``.
        We must echo ``hub.challenge`` if the token matches.
        """
        mode = kwargs.get("hub.mode") or request.params.get("hub.mode")
        token = kwargs.get("hub.verify_token") or request.params.get("hub.verify_token")
        challenge = kwargs.get("hub.challenge") or request.params.get("hub.challenge")

        account = request.env["whatsapp.account"].sudo().browse(account_id).exists()
        if not account:
            _logger.warning("[whatsapp webhook] verify: unknown account_id=%s", account_id)
            return Response("account not found", status=404)

        expected = account.webhook_verify_token or ""
        if mode == "subscribe" and token and token == expected:
            _logger.info("[whatsapp webhook] verify ok for account=%s", account.name)
            return Response(challenge or "", status=200, content_type="text/plain")

        _logger.warning(
            "[whatsapp webhook] verify FAILED for account=%s (mode=%s)",
            account.name,
            mode,
        )
        return Response("forbidden", status=403)

    @http.route(
        "/custom_whatsapp/webhook/<int:account_id>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def webhook_event(self, account_id, **kwargs):
        """Inbound event: status updates + new messages.

        Meta payload shape (abridged)::

            {"object": "whatsapp_business_account",
             "entry": [{"id": "...", "changes": [{"value": {
                 "messaging_product": "whatsapp",
                 "metadata": {...},
                 "contacts": [...],
                 "messages": [...],   # inbound
                 "statuses": [...],   # delivery updates
             }, "field": "messages"}]}]}
        """
        account = request.env["whatsapp.account"].sudo().browse(account_id).exists()
        if not account:
            _logger.warning("[whatsapp webhook] event: unknown account_id=%s", account_id)
            return Response("account not found", status=404)

        raw = request.httprequest.get_data() or b"{}"
        baileys_signature = request.httprequest.headers.get("X-Baileys-Signature") or ""
        baileys_event = request.httprequest.headers.get("X-Baileys-Event") or ""

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as e:
            _logger.warning("[whatsapp webhook] bad json: %s", e)
            return Response("bad request", status=400)

        # Baileys path — distinguished by the X-Baileys-Signature header.
        if baileys_signature:
            secret = account.sudo().baileys_shared_secret or ""
            if not _baileys_signature_valid(secret, raw, baileys_signature):
                _logger.warning(
                    "[whatsapp webhook] baileys HMAC mismatch for account=%s event=%s",
                    account.name,
                    baileys_event,
                )
                return Response("forbidden", status=403)
            try:
                self._dispatch_baileys(account, baileys_event, payload)
            except Exception:
                _logger.exception(
                    "[whatsapp webhook] baileys dispatch failed account=%s event=%s",
                    account.name,
                    baileys_event,
                )
            return Response("ok", status=200, content_type="text/plain")

        # Meta path (default).
        try:
            self._dispatch_payload(account, payload)
        except Exception:
            # Never 500 back to Meta — they retry aggressively and
            # we'd rather see the log than be barraged.
            _logger.exception("[whatsapp webhook] dispatch failed for account=%s", account.name)

        # Always 200 so Meta marks the event delivered.
        return Response("ok", status=200, content_type="text/plain")

    # ----- internals -----

    def _dispatch_payload(self, account, payload: dict):
        Message = request.env["whatsapp.message"].sudo()
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}

                # 1) Delivery / read / failure status updates
                for st in value.get("statuses") or []:
                    try:
                        Message._apply_status_update(st)
                    except Exception:
                        _logger.exception(
                            "[whatsapp webhook] status update failed: %s",
                            st,
                        )

                # 2) Inbound messages from end-users
                contacts = value.get("contacts") or []
                for idx, msg in enumerate(value.get("messages") or []):
                    contact = contacts[idx] if idx < len(contacts) else None
                    try:
                        Message._record_inbound(account, msg, contact)
                    except Exception:
                        _logger.exception(
                            "[whatsapp webhook] inbound record failed: %s",
                            msg,
                        )

    def _dispatch_baileys(self, account, event_type: str, payload: dict):
        """Apply a single Baileys sidecar event.

        Event types (set via ``X-Baileys-Event`` header):

        - ``connection`` — pairing or connection state changed.
        - ``status``     — delivery status update for a previously sent msg.
        - ``message``    — new inbound message from a contact.
        """
        Message = request.env["whatsapp.message"].sudo()
        Account = request.env["whatsapp.account"].sudo()

        if event_type == "status":
            wamid = payload.get("id")
            status = (payload.get("status") or "").lower()
            if not wamid or status not in {"sent", "delivered", "read", "failed"}:
                return
            msg = Message.search([("provider_message_id", "=", wamid)], limit=1)
            if not msg:
                return
            vals = {"state": status}
            if status == "failed":
                vals["error_message"] = (payload.get("error") or "baileys send failed")[:2000]
            msg.write(vals)
            return

        if event_type == "connection":
            new_status = payload.get("status") or "unknown"
            vals = {
                "baileys_status": new_status
                if new_status in {"qr_pending", "connecting", "connected", "disconnected", "error"}
                else "unknown",
            }
            if new_status == "connected":
                vals["baileys_phone"] = payload.get("phone") or False
                vals["baileys_last_qr"] = False
                vals["baileys_last_error"] = False
            else:
                err = payload.get("error")
                if err:
                    vals["baileys_last_error"] = str(err)[:2000]
            Account.browse(account.id).write(vals)
            return

        if event_type == "message":
            msg_payload = payload.get("message") or {}
            from_phone = msg_payload.get("from") or ""
            if not from_phone:
                return
            # Adapt Baileys shape to the existing inbound recorder.
            text = msg_payload.get("text") or ""
            msg_type = msg_payload.get("type") or "text"
            meta_shape = {
                "from": from_phone,
                "id": msg_payload.get("id"),
                "timestamp": msg_payload.get("timestamp"),
                "type": msg_type,
            }
            if msg_type == "text":
                meta_shape["text"] = {"body": text}
            Message._record_inbound(account, meta_shape, None)
            return

        _logger.info(
            "[whatsapp webhook] ignored baileys event_type=%r for account=%s",
            event_type,
            account.name,
        )
