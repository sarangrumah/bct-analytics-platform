# -*- coding: utf-8 -*-
"""payment.transaction extensions for Indonesia gateways.

Hooks:

- :meth:`_send_payment_request` — for midtrans/xendit/doku, invoke the
  adapter to create a Snap token / hosted invoice / checkout URL and
  stash the resulting URL + reference on ``self``.
- :meth:`_get_specific_rendering_values` — return the redirect URL so
  the payment flow can hand the customer over to the gateway.
- :meth:`action_create_refund` — issue a refund against the gateway.

State transitions go through Odoo's documented helpers
(:meth:`_set_pending`, :meth:`_set_done`, :meth:`_set_canceled`,
:meth:`_set_error`) so downstream automation (subscriptions, invoices)
keeps working.
"""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_ID_CODES = ("midtrans", "xendit", "doku", "pos")


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    x_id_redirect_url = fields.Char(
        string="Gateway Redirect URL",
        readonly=True,
        copy=False,
        help="Snap / Invoice / Checkout URL returned by the Indonesia gateway.",
    )
    x_id_raw_response = fields.Text(
        string="Last Gateway Response",
        readonly=True,
        copy=False,
    )

    # -------- Outbound: create checkout --------

    def _send_payment_request(self):
        """Override to route Indonesia providers through their adapters."""
        id_txs = self.filtered(lambda t: t.provider_code in _ID_CODES)
        other = self - id_txs
        if other:
            # Defer to upstream / other providers' inheritance chain.
            super(PaymentTransaction, other)._send_payment_request()
        for tx in id_txs:
            adapter = tx.provider_id._get_id_adapter()
            # _get_id_adapter returns False for non-ID providers, else an
            # AbstractModel recordset (which is falsy — so check identity).
            if adapter is False:
                raise UserError(_("Provider '%s' has no Indonesia adapter.") % tx.provider_id.name)
            result = adapter.create_checkout(tx.provider_id, tx)
            tx.write(
                {
                    "x_id_redirect_url": result.get("redirect_url"),
                    "provider_reference": result.get("reference") or tx.reference,
                    "x_id_raw_response": (str(result.get("raw"))[:65000] if result.get("raw") is not None else False),
                }
            )
            # Pending until the gateway notifies us of success.
            tx._set_pending()

    # -------- Rendering --------

    def _get_specific_rendering_values(self, processing_values):
        """For Indonesia providers, return the redirect URL."""
        self.ensure_one()
        if self.provider_code not in _ID_CODES:
            return super()._get_specific_rendering_values(processing_values)
        return {
            "api_url": self.x_id_redirect_url or "",
            "redirect_url": self.x_id_redirect_url or "",
        }

    # -------- Refund --------

    def action_create_refund(self, amount: float | None = None):
        """Issue a refund through the gateway adapter."""
        self.ensure_one()
        if self.provider_code not in _ID_CODES:
            return super().action_create_refund(amount=amount)
        adapter = self.provider_id._get_id_adapter()
        if not adapter or not hasattr(adapter, "refund"):
            raise UserError(_("Refund not supported for this provider yet."))
        body = adapter.refund(self.provider_id, self, amount=amount)
        self.message_post(body=_("Refund issued via %s: %s") % (self.provider_code, body))
        return body


class PaymentToken(models.Model):
    """Tokenization stub — structure only, no live flow yet.

    Midtrans Snap supports saved-card tokens via its tokenization
    add-on. We expose ``x_id_saved_token_id`` so the future wiring can
    persist Midtrans' returned ``saved_token_id`` without breaking the
    public API. Xendit and DOKU tokenization are out of scope for now.
    """

    _inherit = "payment.token"

    x_id_saved_token_id = fields.Char(
        string="Gateway Saved Token Id",
        copy=False,
        help="Provider-side saved card / wallet token (Midtrans Snap saved-card).",
    )
