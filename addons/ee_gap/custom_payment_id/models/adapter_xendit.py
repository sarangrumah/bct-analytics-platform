# -*- coding: utf-8 -*-
"""Xendit Invoice API adapter.

Real HTTP wiring to the hosted-invoice product. Auth is HTTP Basic
with the secret API key as the username and an empty password.

Endpoint: https://api.xendit.co/v2/invoices

Webhook verification: Xendit echoes the merchant's webhook verification
token in the ``X-Callback-Token`` header on every callback. We compare
that header to ``provider.x_id_webhook_secret``.
"""

from __future__ import annotations

import base64
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class XenditAdapter(models.AbstractModel):
    _name = "custom.payment.id.adapter.xendit"
    _inherit = "custom.payment.id.adapter.base"
    _description = "Xendit Invoice payment gateway adapter"

    # -------- Base adapter overrides --------

    def _base_url(self, provider) -> str:
        # Xendit uses the same hostname for sandbox vs production —
        # mode is determined by the API key itself.
        return "https://api.xendit.co"

    def _endpoint(self, provider, payload: dict) -> str:
        kind = payload.get("_kind") or "invoice"
        return {
            "invoice": "/v2/invoices",
            "va": "/callback_virtual_accounts",
            "ewallet": "/ewallets/charges",
            "qr": "/qr_codes",
        }.get(kind, "/v2/invoices")

    def _auth_headers(self, provider, body_bytes: bytes | None = None) -> dict[str, str]:
        secret = (provider.x_id_server_key or "").strip()
        if not secret:
            raise UserError(_("Xendit Secret API Key is not configured."))
        token = base64.b64encode(f"{secret}:".encode("utf-8")).decode("ascii")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        }

    # -------- High-level: create hosted invoice --------

    def create_invoice(self, provider, transaction) -> dict:
        """Create a Xendit hosted invoice; return ``{id, invoice_url, raw}``."""
        provider.ensure_one()
        transaction.ensure_one()
        payload = self._build_invoice_payload(provider, transaction)
        result = self.send(provider, payload, transaction=transaction)
        if not result["ok"]:
            raise UserError(_("Xendit invoice create failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        body = result["body"] if isinstance(result["body"], dict) else {}
        invoice_id = body.get("id")
        invoice_url = body.get("invoice_url")
        if not invoice_id or not invoice_url:
            raise UserError(_("Xendit invoice response missing id/invoice_url: %s") % body)
        return {"id": invoice_id, "invoice_url": invoice_url, "raw": body}

    def create_checkout(self, provider, transaction) -> dict:
        inv = self.create_invoice(provider, transaction)
        return {
            "redirect_url": inv["invoice_url"],
            "reference": inv["id"],
            "raw": inv["raw"],
        }

    def _build_invoice_payload(self, provider, transaction) -> dict:
        partner = transaction.partner_id
        return {
            "external_id": transaction.reference,
            "amount": int(round(transaction.amount)),
            "payer_email": partner.email or "",
            "description": (transaction.reference or _("Payment via Odoo")),
            "currency": (transaction.currency_id.name or "IDR"),
        }

    # -------- Refund --------

    def refund(self, provider, transaction, amount: float | None = None) -> dict:
        provider.ensure_one()
        transaction.ensure_one()
        ref = transaction.provider_reference or transaction.reference
        if not ref:
            raise UserError(_("Xendit refund needs a provider_reference."))
        payload = {
            "amount": int(round(amount or transaction.amount)),
            "reason": "REQUESTED_BY_CUSTOMER",
        }
        result = self.send(
            provider,
            payload,
            transaction=transaction,
            endpoint_override=f"/payment_requests/{ref}/refunds",
        )
        if not result["ok"]:
            raise UserError(_("Xendit refund failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        return result["body"] if isinstance(result["body"], dict) else {"raw": result["body"]}

    # -------- Webhook verification --------

    @staticmethod
    def verify_callback_token(provided_token: str, expected_token: str) -> bool:
        """Constant-time compare of X-Callback-Token vs configured secret."""
        if not provided_token or not expected_token:
            return False
        if len(provided_token) != len(expected_token):
            return False
        r = 0
        for x, y in zip(provided_token, expected_token, strict=False):
            r |= ord(x) ^ ord(y)
        return r == 0
