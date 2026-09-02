# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class IotReading(models.Model):
    _name = "iot.reading"
    _description = "IoT Sensor Reading"
    _order = "recorded_at desc"

    device_id = fields.Many2one("iot.device", required=True, ondelete="cascade", index=True)
    metric = fields.Char(required=True, index=True, help="Free-form metric key, e.g. 'temperature_c'.")
    value = fields.Float(required=True)
    unit = fields.Char()
    recorded_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    extra = fields.Json(help="Raw payload from device (JSON).")

    # Immutable: only the ingestion controller may write
    def write(self, vals):
        if self.env.context.get("iot_internal_write"):
            return super().write(vals)
        raise UserError(_("IoT readings are immutable."))

    def unlink(self):
        if self.env.context.get("iot_internal_write"):
            return super().unlink()
        raise UserError(_("IoT readings cannot be deleted."))
