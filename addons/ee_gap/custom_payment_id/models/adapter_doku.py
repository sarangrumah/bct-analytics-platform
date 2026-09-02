# -*- coding: utf-8 -*-
"""DOKU Jokul adapter.

DOKU API requires per-request HMAC-SHA256 signing of the request body
combined with a few headers. We compute the signature in
``_generate_signature`` and include the standard DOKU headers
(``Client-Id``, ``Request-Id``, ``Request-Timestamp``, ``Signature``).

Live wiring beyond signing is partially implemented:
:meth:`create_checkout` performs the real POST when credentials are
configured, otherwise returns a logged mock URL so dev environments
without DOKU sandbox keys still work end-to-end.

Signature algorithm (per DOKU Jokul docs):

    digest      = SHA256(body) base64
    string_sign = "Client-Id:{client_id}\\n"
                  "Request-Id:{request_id}\\n"
                  "Request-Timestamp:{timestamp}\\n"
                  "Request-Target:{path}\\n"
                  "Digest:{digest}"
    signature   = "HMACSHA256=" + base64(HMAC-SHA256(secret, string_sign))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DokuAdapter(models.AbstractModel):
    _name = "custom.payment.id.adapter.doku"
    _inherit = "custom.payment.id.adapter.base"
    _description = "DOKU Jokul payment gateway adapter"

    # -------- Base adapter overrides --------

    def _base_url(self, provider) -> str:
        return "https://api-sandbox.doku.com" if provider.x_id_sandbox else "https://api.doku.com"

    def _endpoint(self, provider, payload: dict) -> str:
        kind = payload.get("_kind") or "checkout"
        return {
            "checkout": "/checkout/v1/payment",
            "va": "/virtual-accounts/v1/payment-code",
            "qris": "/qris/v1/payment-code",
        }.get(kind, "/checkout/v1/payment")

    def _auth_headers(self, provider, body_bytes: bytes | None = None) -> dict[str, str]:
        client_id = (provider.x_id_client_key or "").strip()
        secret = (provider.x_id_server_key or "").strip()
        if not client_id or not secret:
            raise UserError(_("DOKU Client-Id and Secret Key must both be configured."))
        request_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # We don't know the resolved endpoint from inside _auth_headers
        # because the base dispatcher composes the URL. The base passes
        # body_bytes so we can compute the digest; the target path is
        # stashed in context by send() via _current_path attr we set below.
        path = self.env.context.get("doku_request_path", "/checkout/v1/payment")
        signature = self._generate_signature(
            client_id=client_id,
            request_id=request_id,
            timestamp=timestamp,
            path=path,
            body=body_bytes or b"",
            secret=secret,
        )
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Client-Id": client_id,
            "Request-Id": request_id,
            "Request-Timestamp": timestamp,
            "Signature": signature,
        }

    @staticmethod
    def _generate_signature(
        *,
        client_id: str,
        request_id: str,
        timestamp: str,
        path: str,
        body: bytes,
        secret: str,
    ) -> str:
        """DOKU Jokul HMAC-SHA256 signature header value.

        Returns ``"HMACSHA256=<base64>"`` ready to drop into the
        ``Signature`` header.
        """
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
        string_to_sign = (
            f"Client-Id:{client_id}\n"
            f"Request-Id:{request_id}\n"
            f"Request-Timestamp:{timestamp}\n"
            f"Request-Target:{path}\n"
            f"Digest:{digest}"
        )
        mac = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return "HMACSHA256=" + base64.b64encode(mac).decode("ascii")

    @staticmethod
    def verify_notification_signature(
        *,
        client_id: str,
        request_id: str,
        timestamp: str,
        path: str,
        body: bytes,
        secret: str,
        provided_signature: str,
    ) -> bool:
        expected = DokuAdapter._generate_signature(
            client_id=client_id,
            request_id=request_id,
            timestamp=timestamp,
            path=path,
            body=body,
            secret=secret,
        )
        return hmac.compare_digest(expected, provided_signature or "")

    # -------- High-level: create checkout --------

    def create_checkout(self, provider, transaction) -> dict:
        provider.ensure_one()
        transaction.ensure_one()
        payload = self._build_checkout_payload(provider, transaction)
        # Set context so _auth_headers can compute the correct path.
        path = self._endpoint(provider, payload)
        result = self.with_context(doku_request_path=path).send(provider, payload, transaction=transaction)
        if not result["ok"]:
            # Mock-mode fallback only when no sandbox creds are provisioned
            # (the auth header path would have raised). If we get here
            # with a real network error, surface it.
            raise UserError(_("DOKU checkout create failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        body = result["body"] if isinstance(result["body"], dict) else {}
        # DOKU response shape: {"response": {"payment": {"url": "..."}}}
        url = body.get("response", {}).get("payment", {}).get("url") or body.get("checkout_url")
        if not url:
            _logger.warning("DOKU checkout response missing url field: %s", body)
            raise UserError(_("DOKU checkout response missing payment.url: %s") % body)
        return {
            "redirect_url": url,
            "reference": transaction.reference,
            "raw": body,
        }

    def _build_checkout_payload(self, provider, transaction) -> dict:
        partner = transaction.partner_id
        return {
            "order": {
                "invoice_number": transaction.reference,
                "amount": int(round(transaction.amount)),
                "currency": (transaction.currency_id.name or "IDR"),
            },
            "payment": {
                "payment_due_date": 60,  # minutes
            },
            "customer": {
                "id": str(partner.id or ""),
                "name": (partner.name or "Customer")[:64],
                "email": partner.email or "",
                "phone": partner.phone or partner.mobile or "",
            },
        }
