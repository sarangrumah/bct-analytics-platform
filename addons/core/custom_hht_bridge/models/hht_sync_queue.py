# -*- coding: utf-8 -*-
# License: LGPL-3
from __future__ import annotations

from odoo import api, fields, models


class HhtSyncQueue(models.Model):
    _name = "hht.sync.queue"
    _description = "HHT Offline Sync Queue Item"
    _order = "received_at desc, id desc"

    device_id = fields.Many2one(
        "hht.device",
        string="Device",
        required=True,
        index=True,
        ondelete="cascade",
    )
    queued_at = fields.Datetime(string="Client-side Queued At")
    received_at = fields.Datetime(
        string="Server-side Received At",
        default=fields.Datetime.now,
        required=True,
    )
    client_id = fields.Char(
        string="Client Operation ID",
        index=True,
        help="Stable client-generated id; used for idempotent de-duplication.",
    )
    payload = fields.Json(string="Raw Payload")
    action = fields.Selection(
        [
            ("receipt", "Receipt"),
            ("issue", "Issue"),
            ("transfer", "Transfer"),
            ("count", "Count"),
            ("handover", "Handover"),
            ("lookup", "Lookup"),
        ],
        index=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("applied", "Applied"),
            ("failed", "Failed"),
            ("deduped", "Deduplicated"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    error = fields.Text()
    batch_id = fields.Char(string="Batch ID", index=True)

    _client_id_device_uniq = models.Constraint(
        "unique(device_id, client_id)",
        "Duplicate client_id for the same device.",
    )

    def action_retry_failed(self):
        # Re-queues failed items for next processing run.
        self.filtered(lambda r: r.state == "failed").write({"state": "queued", "error": False})
        return True

    @api.model
    def _cron_purge_old_queue(self, days: int = 30):
        from datetime import timedelta

        cutoff = fields.Datetime.now() - timedelta(days=days)
        old = self.search(
            [
                ("state", "in", ("applied", "deduped")),
                ("received_at", "<", fields.Datetime.to_string(cutoff)),
            ]
        )
        count = len(old)
        old.unlink()
        return count
