# -*- coding: utf-8 -*-
"""Public share endpoint for read-only dashboard view."""

from __future__ import annotations

import json

from odoo import http
from odoo.http import request


class CustomDashboardShareController(http.Controller):
    @http.route(
        "/custom_dashboard/share/<string:token>",
        type="http",
        auth="public",
        website=False,
        csrf=False,
    )
    def render_share(self, token, **kw):
        if not token:
            return request.not_found()
        Dashboard = request.env["custom.dashboard"].sudo()
        dashboard = Dashboard.search([("share_token", "=", token)], limit=1)
        if not dashboard or not dashboard.is_public:
            return request.not_found()
        tiles = []
        for tile in dashboard.tile_ids:
            payload = {}
            try:
                payload = json.loads(tile.cached_value or "{}")
            except (TypeError, ValueError):
                payload = {}
            tiles.append(
                {
                    "id": tile.id,
                    "name": tile.name,
                    "tile_type": tile.tile_type,
                    "color": tile.color or "#1f77b4",
                    "cached_at": tile.cached_at and tile.cached_at.isoformat() or "",
                    "payload": payload,
                }
            )
        return request.render(
            "custom_dashboards.share_page",
            {
                "dashboard": dashboard,
                "tiles": tiles,
            },
        )
