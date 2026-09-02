# -*- coding: utf-8 -*-
"""POS payment gateway adapter.

Follows the same shape as the Midtrans/Xendit/DOKU adapters but the live
HTTP dispatch is **stubbed** until POS sandbox credentials + the real
endpoint/signature spec arrive. ``create_checkout`` logs a
``custom.payment.id.log`` row (via the shared ``send`` plumbing it would
normally use) and returns a placeholder redirect URL while
``provider.x_id_sandbox`` is set. The retry / circuit-breaker / logging
machinery in the base ``send()`` is preserved and ready to go live: flip the
provider out of sandbox and fill in the real ``_endpoint`` / payload.

Webhook signature placeholder: ``HMAC-SHA256(webhook_secret, raw_body)`` hex,
sent in the ``X-POS-Signature`` header. Adjust once the spec is known.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosAdapter(models.AbstractModel):
    _name = "custom.payment.id.adapter.pos"  # nosemgrep
    _inherit = "custom.payment.id.adapter.base"
    _description = "POS payment gateway adapter (stub)"

    # -------- Base adapter overrides --------

    def _base_url(self, provider) -> str:
        # Placeholder hosts — confirm with POS integration docs.
        return "https://sandbox.payment.pos.com" if provider.x_id_sandbox else "https://payment.pos.com"

    def _endpoint(self, provider, payload: dict) -> str:
        return "/v1/checkout"

    def _auth_headers(self, provider, body_bytes: bytes | None = None) -> dict[str, str]:
        server_key = (provider.x_id_server_key or "").strip()
        if not server_key:
            raise UserError(_("POS Server Key is not configured."))
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-POS-Key": server_key,
            "X-POS-Client": (provider.x_id_client_key or "").strip(),
        }

    # -------- High-level: create checkout --------

    def create_checkout(self, provider, transaction) -> dict:
        """Return ``{redirect_url, reference, raw}``.

        STUB: while the provider is in sandbox we do NOT hit the network.
        We still materialise a log row (so ops can see the attempt) and
        return a placeholder hosted-checkout URL keyed to the reference.
        """
        provider.ensure_one()
        transaction.ensure_one()
        payload = self._build_checkout_payload(provider, transaction)

        if provider.x_id_sandbox:
            log = (
                self.env["custom.payment.id.log"]
                .sudo()
                .create(
                    {
                        "provider_id": provider.id,
                        "transaction_id": transaction.id,
                        "request_payload": json.dumps(payload, ensure_ascii=False, default=str),
                        "state": "ok",
                        "http_status": 200,
                        "response_payload": json.dumps({"stub": True}, default=str),
                    }
                )
            )
            placeholder = f"{self._base_url(provider)}/checkout/{transaction.reference}?stub=1"
            _logger.info(
                "POS STUB checkout for %s -> %s (log %s)",
                transaction.reference,
                placeholder,
                log.id,
            )
            return {
                "redirect_url": placeholder,
                "reference": transaction.reference,
                "raw": {"stub": True, "log_id": log.id},
            }

        # Live path (ready when credentials/spec land).
        result = self.send(provider, payload, transaction=transaction)
        if not result["ok"]:
            raise UserError(_("POS checkout failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        body = result["body"] if isinstance(result["body"], dict) else {}
        redirect = body.get("redirect_url") or body.get("payment_url")
        if not redirect:
            raise UserError(_("POS response missing redirect_url: %s") % body)
        return {"redirect_url": redirect, "reference": transaction.reference, "raw": body}

    def _build_checkout_payload(self, provider, transaction) -> dict:
        partner = transaction.partner_id
        return {
            "order_id": transaction.reference,
            "amount": int(round(transaction.amount)),
            "currency": transaction.currency_id.name or "IDR",
            "customer": {
                "name": (partner.name or "Customer")[:80],
                "email": partner.email or "",
                "phone": partner.phone or "",
            },
        }

    # -------- Webhook signature verification (placeholder) --------

    @staticmethod
    def verify_notification_signature(raw_body: bytes, provided_signature: str, webhook_secret: str) -> bool:
        """Placeholder: HMAC-SHA256(webhook_secret, raw_body) hex, constant-time."""
        if not webhook_secret or not provided_signature:
            return False
        expected = hmac.new(webhook_secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, (provided_signature or "").strip().lower())
