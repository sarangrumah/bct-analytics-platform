# -*- coding: utf-8 -*-
"""Append-only log of every ESB sync run.

Deliberately coarser than ``custom.adapter.call.log`` (which records individual
HTTP calls): this records the *outcome of a feed*, which is what an operator
looks at when asking "did last night's master sync work, and what did it
change?".
"""

from __future__ import annotations

import json

from odoo import api, fields, models

DIRECTIONS = [("pull", "ESB → Odoo"), ("push", "Odoo → ESB")]
STATUSES = [("ok", "OK"), ("error", "Error"), ("skipped", "Skipped")]


class EsbSyncLog(models.Model):
    _name = "custom.esb.sync.log"
    _description = "ESB Sync Log"
    _order = "create_date desc"

    direction = fields.Selection(DIRECTIONS, required=True, default="pull", index=True)
    operation = fields.Char(required=True, index=True, help="Feed or document type, e.g. 'master:branch'.")
    status = fields.Selection(STATUSES, required=True, default="ok", index=True)
    record_count = fields.Integer(help="Rows received from ESB.")
    created_count = fields.Integer()
    updated_count = fields.Integer()
    duration_ms = fields.Integer()
    message = fields.Char()
    payload = fields.Text()
    res_model = fields.Char()
    res_id = fields.Integer()

    @api.model
    def _record(self, direction, operation, status, **kw):
        vals = {
            "direction": direction,
            "operation": operation,
            "status": status,
            "message": (kw.get("message") or "")[:255] or False,
            "record_count": kw.get("record_count") or 0,
            "created_count": kw.get("created_count") or 0,
            "updated_count": kw.get("updated_count") or 0,
            "duration_ms": kw.get("duration_ms") or 0,
            "res_model": kw.get("res_model"),
            "res_id": kw.get("res_id"),
        }
        payload = kw.get("payload")
        if payload is not None:
            try:
                vals["payload"] = json.dumps(payload, default=str)[:65535]
            except (TypeError, ValueError):
                vals["payload"] = str(payload)[:65535]
        return self.sudo().create(vals)
