# -*- coding: utf-8 -*-
import logging
from datetime import datetime

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class IotIngestController(http.Controller):
    @http.route("/iot/ingest", type="jsonrpc", auth="public", methods=["POST"], csrf=False)
    def ingest(self):
        """Webhook for devices to POST a single reading.

        Headers:
          X-Device-Token: <device.api_token>

        Body JSON:
          {"metric": "temperature_c", "value": 24.5, "unit": "C", "recorded_at": "2026-05-17T10:00:00Z", "extra": {...}}
        """
        token = request.httprequest.headers.get("X-Device-Token", "")
        if not token:
            return {"error": "missing_token"}

        device = (
            request.env["iot.device"]
            .sudo()
            .search(
                [("api_token", "=", token), ("active", "=", True)],
                limit=1,
            )
        )
        if not device:
            return {"error": "invalid_token"}

        try:
            payload = request.get_json_data() or {}
        except Exception:
            return {"error": "invalid_payload"}

        metric = payload.get("metric")
        value = payload.get("value")
        if not metric or value is None:
            return {"error": "metric_and_value_required"}

        recorded_at = payload.get("recorded_at")
        if recorded_at:
            try:
                recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                recorded_at = None

        Reading = request.env["iot.reading"].sudo()
        reading = Reading.with_context(iot_internal_write=True).create(
            {
                "device_id": device.id,
                "metric": metric,
                "value": float(value),
                "unit": payload.get("unit"),
                "recorded_at": recorded_at or fields.Datetime.now(),
                "extra": payload.get("extra") or {},
            }
        )
        device.sudo().write(
            {
                "last_seen_at": fields.Datetime.now(),
                "status": "online",
            }
        )

        # Threshold evaluation
        request.env["iot.threshold"].sudo().evaluate(reading)

        return {"ok": True, "reading_id": reading.id}
