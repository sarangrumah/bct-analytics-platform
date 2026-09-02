# -*- coding: utf-8 -*-
"""Public DSAR intake endpoint."""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PdpDsarController(http.Controller):
    @http.route(["/dsar/request"], type="jsonrpc", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def dsar_request(self, **kw):
        payload = kw or {}
        try:
            body = json.loads(request.httprequest.data or b"{}")
            payload.update(body)
        except Exception:
            pass

        subject_email = payload.get("subject_email") or payload.get("email")
        subject_nik = payload.get("subject_nik") or payload.get("nik")
        kind = payload.get("request_kind") or "access"

        if not subject_email:
            return {"ok": False, "error": "subject_email is required"}

        # Try to match an existing partner by email
        partner = (
            request.env["res.partner"]
            .sudo()
            .search(
                [("email", "=ilike", subject_email)],
                limit=1,
            )
        )
        rec = (
            request.env["pdp.dsar.request"]
            .sudo()
            .create(
                {
                    "subject_email": subject_email,
                    "subject_nik": subject_nik,
                    "request_kind": kind,
                    "partner_id": partner.id if partner else False,
                    "state": "received",
                }
            )
        )
        return {
            "ok": True,
            "dsar_id": rec.id,
            "reference": rec.name,
            "state": rec.state,
        }
