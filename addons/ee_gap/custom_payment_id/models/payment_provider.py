# -*- coding: utf-8 -*-
"""Register Midtrans/Xendit/DOKU on payment.provider.

In Odoo 19 the discriminator field is ``payment.provider.code`` (a
Selection). We extend it via ``selection_add`` and attach the local
configuration fields. Credentials are stored on the provider rather
than a separate config record to align with stock Odoo conventions.

Sensitive credential fields (server_key, webhook_secret) are gated via
``groups="custom_payment_id.group_manager"`` instead of ``password=True``
per platform rules.
"""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[
            ("midtrans", "Midtrans"),
            ("xendit", "Xendit"),
            ("doku", "DOKU"),
            ("pos", "POS"),
        ],
        ondelete={
            "midtrans": "set default",
            "xendit": "set default",
            "doku": "set default",
            "pos": "set default",
        },
    )

    # ----- Indonesia gateway credentials (shared field set) -----
    x_id_server_key = fields.Char(
        string="Server / Secret Key",
        help="Midtrans Server Key, Xendit Secret Key, or DOKU Secret Key.",
        groups="custom_payment_id.group_manager",
    )
    x_id_client_key = fields.Char(
        string="Client / Public Key",
        help="Midtrans Client Key, Xendit Public Key, or DOKU Client Id.",
    )
    x_id_merchant_id = fields.Char(
        string="Merchant Id",
        help="DOKU merchant id. Optional for Midtrans/Xendit.",
    )
    x_id_sandbox = fields.Boolean(
        string="Sandbox Mode",
        default=True,
        help="Use sandbox endpoints. Disable for production.",
    )
    x_id_webhook_secret = fields.Char(
        string="Webhook Secret / Callback Token",
        help=(
            "Midtrans: ignored (signature uses server_key). "
            "Xendit: X-Callback-Token verification token. "
            "DOKU: shared secret for notification HMAC verification."
        ),
        groups="custom_payment_id.group_manager",
    )

    # -------- Helpers --------

    def _get_id_adapter(self):
        """Return the concrete Indonesia adapter model for ``self``."""
        self.ensure_one()
        if self.code not in ("midtrans", "xendit", "doku", "pos"):
            return False
        return self.env["custom.payment.id.adapter.base"]._get_for_provider(self)

    def action_test_id_connection(self):
        """UI button — round-trip a ping request through the adapter."""
        self.ensure_one()
        adapter = self._get_id_adapter()
        if adapter is False:
            raise UserError(_("Provider '%s' is not an Indonesia gateway.") % self.name)
        # Use the sudo-protected server key field requires manager group;
        # surface a friendly error when missing rather than failing later.
        if not self.sudo().x_id_server_key:
            raise UserError(_("Configure the Server Key first."))
        try:
            result = adapter.test_connection(self)
        except Exception as e:  # noqa: BLE001 — surface to UI
            raise UserError(_("Test failed: %s") % e) from e
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection Test"),
                "message": _("HTTP %(status)s in %(latency)d ms (log id %(log)s)")
                % {
                    "status": result.get("http_status"),
                    "latency": result.get("latency_ms") or 0,
                    "log": result.get("log_id"),
                },
                "type": "success" if result.get("ok") else "warning",
                "sticky": False,
            },
        }
