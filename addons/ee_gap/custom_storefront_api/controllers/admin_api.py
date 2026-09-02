# -*- coding: utf-8 -*-
"""Server-to-server admin API for the Next.js BFF.

Guarded by ``@secure_endpoint('storefront')`` (HMAC-SHA256 over
``ascii(timestamp)+raw_body`` with nonce replay protection + IP whitelist —
see custom_core). The HMAC secret lives only in Odoo's config params and the
BFF's environment; it is never exposed to the browser.
"""

from __future__ import annotations

import logging

from odoo import http
from odoo.http import request

from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint

from .cors import json_body, json_response

_logger = logging.getLogger(__name__)


class StorefrontAdminController(http.Controller):
    @http.route(
        "/storefront/api/admin/health",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @secure_endpoint("storefront")
    def health(self, **kw):
        return json_response({"ok": True, "service": "custom_storefront_api"})

    @http.route(
        "/storefront/api/admin/sync/products",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @secure_endpoint("storefront")
    def sync_products(self, **kw):
        body = json_body()
        try:
            limit = min(int(body.get("limit", 500)), 2000)
            offset = max(int(body.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return json_response({"ok": False, "error_code": "BAD_PAGINATION"}, status=400)
        Product = request.env["product.template"].sudo()
        domain = [("sale_ok", "=", True)]
        total = Product.search_count(domain)
        records = Product.search(domain, limit=limit, offset=offset, order="id")
        items = [p._storefront_serialize(detail=True) for p in records]
        return json_response({"ok": True, "data": {"items": items, "total": total}})

    @http.route(
        "/storefront/api/admin/orders/<int:order_id>/status",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @secure_endpoint("storefront")
    def order_status(self, order_id, **kw):
        order = request.env["sale.order"].sudo().browse(order_id)
        if not order.exists():
            return json_response({"ok": False, "error_code": "NOT_FOUND"}, status=404)
        tx = (
            request.env["payment.transaction"]
            .sudo()
            .search([("sale_order_ids", "in", order.id)], order="id desc", limit=1)
        )
        return json_response(
            {
                "ok": True,
                "data": {
                    "order_id": order.id,
                    "name": order.name,
                    "state": order.state,
                    "amount_total": order.amount_total,
                    "payment_state": tx.state if tx else None,
                    "awb_number": order.x_awb_number or None,
                },
            }
        )
