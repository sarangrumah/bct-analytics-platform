# -*- coding: utf-8 -*-
from odoo import api, fields, models


CONDITIONS = [
    (">", ">"),
    ("<", "<"),
    (">=", ">="),
    ("<=", "<="),
    ("==", "=="),
]


class IotThreshold(models.Model):
    _name = "iot.threshold"
    _description = "IoT Threshold Rule"
    _order = "device_id, metric"
    _inherit = ["pdp.audited.mixin"]

    device_id = fields.Many2one("iot.device", required=True, ondelete="cascade", index=True)
    metric = fields.Char(required=True)
    condition = fields.Selection(CONDITIONS, required=True, default=">")
    threshold_value = fields.Float(required=True)
    severity = fields.Selection(
        [("info", "Info"), ("warn", "Warning"), ("critical", "Critical")],
        default="warn",
        required=True,
    )
    notify_user_ids = fields.Many2many(
        "res.users",
        "iot_threshold_user_rel",
        "threshold_id",
        "user_id",
    )
    alert_active = fields.Boolean(default=False, readonly=True)
    alert_since = fields.Datetime(readonly=True)
    last_evaluated_at = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)

    def _pdp_audit_classification(self):
        return "internal"

    @api.model
    def evaluate(self, reading):
        """Evaluate all thresholds for the (device, metric) of ``reading``."""
        thresholds = self.sudo().search(
            [
                ("device_id", "=", reading.device_id.id),
                ("metric", "=", reading.metric),
                ("active", "=", True),
            ]
        )
        for t in thresholds:
            v = reading.value
            tv = t.threshold_value
            ok = {
                ">": v > tv,
                "<": v < tv,
                ">=": v >= tv,
                "<=": v <= tv,
                "==": v == tv,
            }.get(t.condition, False)
            now = fields.Datetime.now()
            if ok and not t.alert_active:
                t.write({"alert_active": True, "alert_since": now, "last_evaluated_at": now})
                t.device_id.message_post(
                    body=f"⚠ Threshold breached on {t.metric}: {v} {t.condition} {tv} (severity: {t.severity})",
                )
                t._pdp_audit_write("iot_threshold_trip", t.id, {"metric": t.metric, "value": v, "severity": t.severity})
            elif not ok and t.alert_active:
                t.write({"alert_active": False, "last_evaluated_at": now})
                t.device_id.message_post(body=f"✓ {t.metric} back within range.")
                t._pdp_audit_write("iot_threshold_clear", t.id, {"metric": t.metric, "value": v})
            else:
                t.write({"last_evaluated_at": now})
