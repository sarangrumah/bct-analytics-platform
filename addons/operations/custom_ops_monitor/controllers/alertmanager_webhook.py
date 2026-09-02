# -*- coding: utf-8 -*-
"""Alertmanager webhook receiver.

Uses the platform's ``secure_endpoint`` decorator from
``custom_core.controllers.secure_endpoint`` when available, with
HMAC-SHA256 signing scoped to ``ops_alertmanager``. The configured
secret lives in ``ir.config_parameter`` key
``custom_core.secure_endpoint.ops_alertmanager.secret``.

Payload follows Alertmanager webhook v4 spec:
https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
"""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.http import request

try:
    from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

    _HAS_SECURE = True
except ImportError:  # pragma: no cover
    _HAS_SECURE = False

    def secure_endpoint(scope):  # type: ignore
        def _wrap(func):
            return func

        return _wrap


_logger = logging.getLogger(__name__)


class AlertmanagerWebhookController(http.Controller):
    @http.route(
        "/api/ops/alert",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @secure_endpoint("ops_alertmanager")
    def receive_alert(self, **kwargs):
        body = request.httprequest.get_data() or b""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except ValueError as e:
            return request.make_json_response(
                {"ok": False, "error_code": "BAD_JSON", "detail": str(e)},
                status=400,
            )

        # Defensive validation: must be a dict with an "alerts" list.
        if not isinstance(payload, dict) or not isinstance(
            payload.get("alerts"),
            list,
        ):
            return request.make_json_response(
                {"ok": False, "error_code": "BAD_PAYLOAD"},
                status=400,
            )

        Incident = request.env["custom.ops.incident"].sudo()
        try:
            touched = Incident.ingest_alertmanager_payload(payload)
        except Exception as e:  # pragma: no cover - defensive
            _logger.exception("alertmanager ingest failed")
            return request.make_json_response(
                {"ok": False, "error_code": "INGEST_FAILED", "detail": str(e)[:300]},
                status=500,
            )
        return request.make_json_response(
            {"ok": True, "incident_count": len(touched)},
            status=200,
        )
