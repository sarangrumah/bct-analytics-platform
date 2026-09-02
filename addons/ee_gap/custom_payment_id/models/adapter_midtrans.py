# -*- coding: utf-8 -*-
"""Midtrans Snap API adapter.

Real HTTP wiring to Midtrans Snap (Indonesia's most-used checkout
product). Server key auth via HTTP Basic with a trailing colon as
documented in Midtrans Snap reference.

Sandbox:   https://app.sandbox.midtrans.com
Production: https://app.midtrans.com

Webhook signature uses SHA-512 of ``order_id + status_code +
gross_amount + server_key`` per Midtrans HTTP Notification spec.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MidtransAdapter(models.AbstractModel):
    _name = "custom.payment.id.adapter.midtrans"
    _inherit = "custom.payment.id.adapter.base"
    _description = "Midtrans Snap payment gateway adapter"

    # -------- Base adapter overrides --------

    def _base_url(self, provider) -> str:
        # Snap endpoints live on app.* (NOT api.*) per Midtrans docs.
        return "https://app.sandbox.midtrans.com" if provider.x_id_sandbox else "https://app.midtrans.com"

    def _endpoint(self, provider, payload: dict) -> str:
        return "/snap/v1/transactions"

    def _auth_headers(self, provider, body_bytes: bytes | None = None) -> dict[str, str]:
        server_key = (provider.x_id_server_key or "").strip()
        if not server_key:
            raise UserError(_("Midtrans Server Key is not configured."))
        # NOTE the trailing colon — Midtrans uses server_key as the
        # Basic username with an empty password.
        token = base64.b64encode(f"{server_key}:".encode("utf-8")).decode("ascii")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        }

    # -------- High-level: create Snap token --------

    def create_snap_token(self, provider, transaction) -> dict:
        """Create a Snap transaction; return ``{token, redirect_url, raw}``.

        ``transaction`` is a ``payment.transaction`` recordset.
        """
        provider.ensure_one()
        transaction.ensure_one()
        payload = self._build_snap_payload(provider, transaction)
        result = self.send(provider, payload, transaction=transaction)
        if not result["ok"]:
            raise UserError(_("Midtrans Snap create failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        body = result["body"] if isinstance(result["body"], dict) else {}
        token = body.get("token")
        redirect = body.get("redirect_url")
        if not token or not redirect:
            raise UserError(_("Midtrans Snap response missing token/redirect_url: %s") % body)
        return {"token": token, "redirect_url": redirect, "raw": body}

    def create_checkout(self, provider, transaction) -> dict:
        """Adapter-base contract: return ``{redirect_url, reference, raw}``."""
        snap = self.create_snap_token(provider, transaction)
        return {
            "redirect_url": snap["redirect_url"],
            "reference": transaction.reference,
            "raw": snap["raw"],
        }

    def _build_snap_payload(self, provider, transaction) -> dict:
        partner = transaction.partner_id
        gross = int(round(transaction.amount))  # Midtrans expects integer IDR
        return {
            "transaction_details": {
                "order_id": transaction.reference,
                "gross_amount": gross,
            },
            "customer_details": {
                "first_name": (partner.name or "Customer")[:50],
                "email": partner.email or "",
                "phone": partner.phone or partner.mobile or "",
            },
            "item_details": [
                {
                    "id": transaction.reference,
                    "name": (transaction.reference or "Payment")[:50],
                    "price": gross,
                    "quantity": 1,
                }
            ],
            "credit_card": {"secure": True},
        }

    # -------- Refund (Core API path) --------

    def refund(self, provider, transaction, amount: float | None = None) -> dict:
        """Refund via Core API. Returns the parsed JSON body.

        Midtrans refund lives under ``api.*`` (Core API), not ``app.*``
        (Snap). We dispatch via the shared :meth:`send` plumbing using a
        small adapter shim so logging + retry still apply.
        """
        provider.ensure_one()
        transaction.ensure_one()
        ref = transaction.provider_reference or transaction.reference
        if not ref:
            raise UserError(_("Midtrans refund needs a provider_reference."))
        payload = {
            "refund_key": f"refund-{transaction.id}",
            "amount": int(round(amount or transaction.amount)),
            "reason": "Refund via Odoo",
        }
        endpoint_override = f"/v2/{ref}/refund"
        # Swap to Core API host for this call only.
        shim = self.env["custom.payment.id.adapter.midtrans.core"]
        result = shim.send(
            provider,
            payload,
            transaction=transaction,
            endpoint_override=endpoint_override,
        )
        if not result["ok"]:
            raise UserError(_("Midtrans refund failed (HTTP %s): %s") % (result["http_status"], result["body"]))
        return result["body"] if isinstance(result["body"], dict) else {"raw": result["body"]}

    # -------- Webhook signature verification --------

    @staticmethod
    def verify_notification_signature(
        order_id: str,
        status_code: str,
        gross_amount: str,
        server_key: str,
        signature_key: str,
    ) -> bool:
        """Verify Midtrans HTTP Notification signature.

        ``signature_key = SHA512(order_id + status_code + gross_amount + server_key)``
        """
        if not all([order_id, status_code, gross_amount, server_key, signature_key]):
            return False
        raw = f"{order_id}{status_code}{gross_amount}{server_key}".encode("utf-8")
        expected = hashlib.sha512(raw).hexdigest()
        # Constant-time compare
        return _consteq(expected, signature_key.lower())


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b, strict=False):
        r |= ord(x) ^ ord(y)
    return r == 0


class MidtransCoreAdapter(models.AbstractModel):
    """Core API (api.midtrans.com) shim for refund / status / capture.

    Reuses the Snap adapter's auth headers but points at api.* host.
    """

    _name = "custom.payment.id.adapter.midtrans.core"
    _inherit = "custom.payment.id.adapter.midtrans"
    _description = "Midtrans Core API adapter (refund/status)"

    def _base_url(self, provider) -> str:
        return "https://api.sandbox.midtrans.com" if provider.x_id_sandbox else "https://api.midtrans.com"

    def _endpoint(self, provider, payload: dict) -> str:
        # endpoint_override is always supplied for this shim.
        return "/v2/charge"
