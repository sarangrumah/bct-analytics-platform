# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import fields, models
from odoo.exceptions import UserError


class CustomAdapterCallLog(models.Model):
    _name = "custom.adapter.call.log"
    _description = "Adapter Call Log (append-only)"
    _order = "called_at desc, id desc"
    _rec_name = "endpoint"

    config_id = fields.Many2one(
        "custom.adapter.config",
        required=True,
        ondelete="restrict",
        index=True,
    )
    endpoint = fields.Char(required=True, index=True)
    request_hash = fields.Char(string="SHA-256(body)", index=True)
    response_status = fields.Integer(index=True)
    latency_ms = fields.Integer()
    error = fields.Char()
    ok = fields.Boolean(index=True)
    called_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)

    def write(self, vals):
        raise UserError("custom.adapter.call.log is append-only.")

    def unlink(self):
        if not self.env.is_superuser():
            raise UserError("custom.adapter.call.log is append-only.")
        return super().unlink()
