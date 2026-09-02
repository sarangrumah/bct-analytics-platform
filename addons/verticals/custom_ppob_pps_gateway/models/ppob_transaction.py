# -*- coding: utf-8 -*-
"""Extend custom.ppob.transaction with PPS-gateway state: the ServerIDTrx, the
game dynamic-field payload, and the async outbound-callback lifecycle.

Callbacks are decoupled from _mark_success/_mark_failed (which run inside the
money-movement DB transaction holding wallet/bucket row locks): those methods
only FLAG the callback; a dedicated cron fires the HTTP GET so a slow POS
endpoint can never hold locks or roll back a successful sell.
"""

import logging

from odoo import api, fields, models

from ..pps_errors import state_to_status
from ..pps_message import sale_message

try:
    import requests
except ImportError:
    requests = None

_logger = logging.getLogger(__name__)

_CALLBACK_MAX_ATTEMPTS = 5
_CALLBACK_TIMEOUT_S = 10


class PpobTransaction(models.Model):
    _inherit = "custom.ppob.transaction"

    pps_serveridtrx = fields.Char(
        string="PPS ServerIDTrx",
        index=True,
        copy=False,
        help="Numeric server transaction id returned to POS. Set for gateway (PPS-originated) transactions only.",
    )
    pps_produk = fields.Char(
        string="PPS Produk",
        copy=False,
        help="The 'produk' code POS sent (echoed on the callback).",
    )
    dynamic_field = fields.Json(
        string="Game Dynamic Fields",
        copy=False,
        help="direct-topup 'field' object (userid/zoneid/server).",
    )
    pps_callback_url = fields.Char(copy=False)
    pps_callback_state = fields.Selection(
        selection=[
            ("none", "N/A"),
            ("pending", "Pending"),
            ("sent", "Sent"),
            ("failed", "Failed (gave up)"),
        ],
        default="none",
        required=True,
        index=True,
        copy=False,
    )
    pps_callback_attempts = fields.Integer(default=0, copy=False)
    pps_last_callback_at = fields.Datetime(copy=False)

    # ------------------------------------------------------------------
    # Callback flagging (called from terminal transitions)
    # ------------------------------------------------------------------

    def _pps_flag_callback(self):
        for t in self:
            if t.pps_serveridtrx and t.pps_callback_state == "none" and t.pps_callback_url:
                t.pps_callback_state = "pending"

    def _mark_success(self, provider_ref=None, serial_token=None):
        res = super()._mark_success(provider_ref=provider_ref, serial_token=serial_token)
        self._pps_flag_callback()
        return res

    def _mark_failed(self, error_code=None, error_message=None):
        res = super()._mark_failed(error_code=error_code, error_message=error_message)
        self._pps_flag_callback()
        return res

    # ------------------------------------------------------------------
    # Outbound callback dispatcher (cron)
    # ------------------------------------------------------------------

    @api.model
    def _cron_pps_dispatch_callbacks(self, batch_size=200):
        """Fire the PPS GET callback for terminal, pending-callback gateway
        transactions. Meets the <=60s SLA at a 1-minute cron cadence."""
        txns = self.search(
            [
                ("pps_callback_state", "=", "pending"),
                ("state", "in", ("success", "failed", "timeout", "refunded")),
            ],
            limit=batch_size,
        )
        for txn in txns:
            txn._pps_fire_callback()

    def _pps_fire_callback(self):
        self.ensure_one()
        Log = self.env["custom.ppob.pps.callback.log"]
        if not self.pps_callback_url:
            self.pps_callback_state = "failed"
            return False
        if requests is None:
            _logger.error("PPS callback: 'requests' not available")
            return False

        status = state_to_status(self.state)  # "0" success / "1" failed
        params = {
            "serveridtrx": self.pps_serveridtrx or "",
            "clientnotrx": self.idempotency_key or "",
            "status": status,
            "produk": self.pps_produk or (self.product_id.code or ""),
            "mdn": self.msisdn or "",
            "sn": self.serial_token or "",
            "message": sale_message(self),
        }
        attempt = self.pps_callback_attempts + 1
        http_status = 0
        error = False
        ok = False
        try:
            resp = requests.get(self.pps_callback_url, params=params, timeout=_CALLBACK_TIMEOUT_S)
            http_status = resp.status_code
            ok = 200 <= resp.status_code < 300
        except Exception as exc:  # network / timeout
            error = str(exc)[:512]
            _logger.warning("PPS callback failed for %s: %s", self.name, error)

        Log.create(
            {
                "transaction_id": self.id,
                "url": self.pps_callback_url,
                "querystring": "&".join(f"{k}={v}" for k, v in params.items()),
                "http_status": http_status,
                "attempt_no": attempt,
                "ok": ok,
                "error": error,
            }
        )
        vals = {
            "pps_callback_attempts": attempt,
            "pps_last_callback_at": fields.Datetime.now(),
        }
        if ok:
            vals["pps_callback_state"] = "sent"
        elif attempt >= _CALLBACK_MAX_ATTEMPTS:
            vals["pps_callback_state"] = "failed"
            _logger.warning(
                "PPS callback for %s gave up after %s attempts (POS must poll StatusTrx).", self.name, attempt
            )
        self.write(vals)
        return ok

    # ------------------------------------------------------------------
    # Gateway helpers
    # ------------------------------------------------------------------

    @api.model
    def _pps_next_serveridtrx(self):
        return self.env["ir.sequence"].next_by_code("custom.ppob.pps.serveridtrx") or "0"
