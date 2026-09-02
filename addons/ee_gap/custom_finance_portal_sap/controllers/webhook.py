# -*- coding: utf-8 -*-
"""Inbound webhook for the SAP bridge → Portal (realtime status + master delta).

Guarded by ``secure_endpoint('finance_sap')`` from ``custom_core``: HMAC-SHA256
over ``timestamp || body``, ±300s drift, replay-nonce and optional CIDR
whitelist. The bridge signs with the same secret the Odoo adapter uses, so the
contract is symmetric.

Configure: ``custom_core.secure_endpoint.finance_sap.secret`` (and optionally
``...finance_sap.allowed_cidrs``).
"""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

_logger = logging.getLogger(__name__)


class FinanceSapWebhook(http.Controller):
    @http.route("/finance/sap/status", type="http", auth="public", methods=["POST"], csrf=False)
    @secure_endpoint("finance_sap")
    def sap_status(self, **kwargs):
        """Mirror a SAP payment-plan date / payment status onto a document."""
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except (ValueError, TypeError):
            return request.make_json_response({"ok": False, "error": "BAD_JSON"}, status=400)
        ok = request.env["finance.sync.log"].sudo()._apply_status_in(payload)
        return request.make_json_response({"ok": bool(ok)}, status=200 if ok else 422)

    @http.route("/finance/sap/master", type="http", auth="public", methods=["POST"], csrf=False)
    @secure_endpoint("finance_sap")
    def sap_master(self, **kwargs):
        """Realtime master-data delta push (single feed)."""
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except (ValueError, TypeError):
            return request.make_json_response({"ok": False, "error": "BAD_JSON"}, status=400)
        kind = payload.get("kind")
        records = payload.get("records", [])
        handlers = {
            "division": "_upsert_divisions",
            "item_category": "_upsert_item_categories",
            "supplier": "_upsert_suppliers",
            "budget": "_upsert_budgets",
            "travel": "_upsert_travel",
        }
        handler = handlers.get(kind)
        Log = request.env["finance.sync.log"].sudo()
        if not handler:
            Log._record("pull", "master:%s" % kind, "error", "Unknown feed", payload=payload)
            return request.make_json_response({"ok": False, "error": "UNKNOWN_FEED"}, status=400)
        count = getattr(Log, handler)(records)
        Log._record("pull", "master:%s" % kind, "ok", record_count=count)
        return request.make_json_response({"ok": True, "count": count}, status=200)
