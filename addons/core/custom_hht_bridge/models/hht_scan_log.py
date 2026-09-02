# -*- coding: utf-8 -*-
# License: LGPL-3
from __future__ import annotations

from odoo import fields, models


class HhtScanLog(models.Model):
    _name = "hht.scan.log"
    _description = "HHT Scan Audit Log (append-only)"
    _order = "scanned_at desc, id desc"

    device_id = fields.Many2one(
        "hht.device",
        string="Device",
        required=True,
        index=True,
        ondelete="restrict",
    )
    scanned_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    barcode = fields.Char(index=True)
    action = fields.Selection(
        [
            ("receipt", "Receipt"),
            ("issue", "Issue"),
            ("transfer", "Transfer"),
            ("count", "Count"),
            ("handover", "Handover"),
            ("lookup", "Lookup"),
        ],
        required=True,
        default="lookup",
        index=True,
    )
    location_id = fields.Many2one("stock.location", string="Location")
    qty = fields.Float(string="Quantity", digits="Product Unit of Measure")
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial")
    picking_id = fields.Many2one("stock.picking", string="Transfer")
    result = fields.Selection(
        [
            ("ok", "OK"),
            ("error", "Error"),
            ("pending_sync", "Pending Sync"),
        ],
        required=True,
        default="ok",
        index=True,
    )
    error_message = fields.Text()
    payload = fields.Json(string="Raw Request Payload")
    client_ip = fields.Char(string="Client IP")

    def init(self):
        # Composite index for hot-path device timeline queries.
        tools = self.env.cr
        tools.execute(
            "CREATE INDEX IF NOT EXISTS hht_scan_log_device_time_idx ON hht_scan_log (device_id, scanned_at DESC)"
        )

    def name_get(self):
        return [
            (rec.id, "%s · %s · %s" % (rec.device_id.display_name or "?", rec.action, rec.barcode or "—"))
            for rec in self
        ]
