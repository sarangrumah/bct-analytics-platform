# -*- coding: utf-8 -*-
import secrets

from odoo import _, api, fields, models


DEVICE_KINDS = [
    ("sensor", "Sensor"),
    ("gateway", "Gateway"),
    ("plc", "PLC"),
    ("camera", "Camera"),
    ("other", "Other"),
]


class IotDevice(models.Model):
    _name = "iot.device"
    _description = "IoT Device"
    _order = "name"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    kind = fields.Selection(DEVICE_KINDS, default="sensor", required=True)
    location = fields.Char()
    api_token = fields.Char(
        readonly=True, copy=False, index=True, help="Required in POST /iot/ingest header X-Device-Token."
    )
    last_seen_at = fields.Datetime(readonly=True)
    status = fields.Selection(
        [("online", "Online"), ("offline", "Offline"), ("decommissioned", "Decommissioned")],
        default="offline",
        required=True,
        tracking=True,
    )
    threshold_ids = fields.One2many("iot.threshold", "device_id")
    reading_count = fields.Integer(compute="_compute_counts")
    alert_count = fields.Integer(compute="_compute_counts")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Device code must be unique.",
    )

    def _compute_counts(self):
        R = self.env["iot.reading"].sudo()
        T = self.env["iot.threshold"].sudo()
        for rec in self:
            rec.reading_count = R.search_count([("device_id", "=", rec.id)])
            rec.alert_count = T.search_count(
                [("device_id", "=", rec.id), ("alert_active", "=", True)],
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("api_token"):
                vals["api_token"] = secrets.token_urlsafe(32)
        return super().create(vals_list)

    def action_rotate_token(self):
        for rec in self:
            rec.api_token = secrets.token_urlsafe(32)
            rec.message_post(body=_("API token rotated."))
