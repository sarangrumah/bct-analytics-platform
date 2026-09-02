# -*- coding: utf-8 -*-
"""Extend custom.ppob.provider with a bridge_mode flag + Oracle SP name."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PpobProvider(models.Model):
    _inherit = "custom.ppob.provider"

    bridge_mode = fields.Selection(
        selection=[
            ("h2h_direct", "H2H Direct (Odoo native)"),
            ("oracle_bridge", "Oracle Bridge (via MSG016T)"),
        ],
        default="h2h_direct",
        required=True,
        help="Routing mode for this provider. h2h_direct: Odoo dispatches via "
        "the registered adapter. oracle_bridge: Odoo calls the Oracle SP, "
        "status polled from MSG016T.",
    )
    oracle_sp_name = fields.Char(
        string="Oracle SP Name",
        default="SellWithDenom_HA",
        help="Stored procedure used by the Oracle bridge adapter's pay(). Schema prefix is added from the connection.",
    )

    @api.constrains("bridge_mode")
    def _check_bridge_mode_no_pending(self):
        Txn = self.env["custom.ppob.transaction"]
        for rec in self:
            pending = Txn.search_count(
                [
                    ("provider_id", "=", rec.id),
                    ("state", "in", ("pending", "inquiry_ok", "in_progress")),
                ]
            )
            if pending:
                raise UserError(
                    _("Tidak bisa mengubah bridge_mode provider %s: ada %s transaksi yang belum selesai.")
                    % (rec.code, pending)
                )

    @api.onchange("bridge_mode")
    def _onchange_bridge_mode(self):
        if self.bridge_mode == "oracle_bridge":
            self.adapter_class = "ppob_oracle_bridge"
        elif self.bridge_mode == "h2h_direct" and self.adapter_class == "ppob_oracle_bridge":
            self.adapter_class = "ppob_http_json"
