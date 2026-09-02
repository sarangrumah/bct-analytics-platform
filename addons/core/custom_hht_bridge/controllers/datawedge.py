# -*- coding: utf-8 -*-
# License: LGPL-3
"""Zebra DataWedge ingest endpoint.

DataWedge wedge-style scanners may not be able to compute HMAC signatures
themselves. This endpoint accepts a simpler payload bound to the device
serial and is guarded by an IP allow-list (separate from `secure_endpoint`).
"""

from __future__ import annotations

import json
import logging
from ipaddress import ip_address, ip_network

from odoo import http
from odoo.http import request

from .api import _handle_scan, _json, _log_scan

_logger = logging.getLogger(__name__)


def _ip_allowed() -> bool:
    allowed = (
        request.env["ir.config_parameter"]
        .sudo()
        .get_param(
            "custom_hht_bridge.datawedge.allowed_cidrs",
            "0.0.0.0/0",
        )
        or ""
    )
    if not allowed.strip():
        return True
    httpreq = request.httprequest
    remote = (httpreq.environ.get("HTTP_X_FORWARDED_FOR") or httpreq.remote_addr or "").split(",")[0].strip()
    if not remote:
        return False
    try:
        addr = ip_address(remote)
    except ValueError:
        return False
    for chunk in allowed.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "/" in chunk:
                if addr in ip_network(chunk, strict=False):
                    return True
            elif addr == ip_address(chunk):
                return True
        except ValueError:
            continue
    return False


class HhtDataWedge(http.Controller):
    @http.route("/api/hht/datawedge", type="http", auth="public", methods=["POST"], csrf=False)
    def datawedge(self, **kw):
        if not _ip_allowed():
            return _json({"ok": False, "error": "IP_NOT_ALLOWED"}, status=403)
        httpreq = request.httprequest
        ctype = (httpreq.headers.get("Content-Type") or "").lower()
        data: dict = {}
        if "application/json" in ctype:
            try:
                data = json.loads(httpreq.get_data().decode("utf-8") or "{}") or {}
            except (ValueError, UnicodeDecodeError):
                data = {}
        else:
            # form-encoded or query-string fallback.
            data = {k: v for k, v in kw.items() if isinstance(v, (str, int, float))}
        serial = (data.get("device_serial") or "").strip()
        barcode = (data.get("barcode") or "").strip()
        if not serial or not barcode:
            return _json({"ok": False, "error": "MISSING_FIELDS"})
        device = request.env["hht.device"].sudo()._find_by_serial(serial)
        if not device:
            _log_scan(
                None,
                action="lookup",
                barcode=barcode,
                result="error",
                error_message="UNKNOWN_DEVICE_SERIAL",
                payload=data,
            )
            return _json({"ok": False, "error": "UNKNOWN_DEVICE_SERIAL"})
        # Wrap into the canonical scan handler with action=lookup.
        outcome = _handle_scan(
            device,
            {
                "barcode": barcode,
                "action": data.get("action") or "lookup",
            },
        )
        return _json(outcome)
