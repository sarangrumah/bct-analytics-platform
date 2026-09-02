# -*- coding: utf-8 -*-
"""KPI dashboard container — owns tiles and exposes the Ask-AI NLQ entry point."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomDashboard(models.Model):
    _name = "custom.dashboard"
    _description = "Custom Dashboard"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    owner_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        required=True,
        tracking=True,
    )
    description = fields.Text()
    is_published = fields.Boolean(default=False, tracking=True)
    is_public = fields.Boolean(
        default=False,
        tracking=True,
        help="Public dashboards can be viewed read-only via the share link.",
    )
    allowed_group_ids = fields.Many2many(
        "res.groups",
        "custom_dashboard_group_rel",
        "dashboard_id",
        "group_id",
        string="Allowed Groups",
    )
    tile_ids = fields.One2many(
        "custom.dashboard.tile",
        "dashboard_id",
        string="Tiles",
    )
    tile_count = fields.Integer(compute="_compute_tile_count")
    color = fields.Integer()

    # Share link
    share_token = fields.Char(readonly=True, copy=False, index=True)
    share_url = fields.Char(compute="_compute_share_url")

    # AI NLQ
    last_ai_question = fields.Char()
    last_ai_answer = fields.Html(readonly=True, sanitize=True)
    last_ai_at = fields.Datetime(readonly=True)

    _share_token_uniq = models.Constraint(
        "unique(share_token)",
        "Share token must be unique.",
    )

    @api.depends("tile_ids")
    def _compute_tile_count(self):
        for rec in self:
            rec.tile_count = len(rec.tile_ids)

    @api.depends("share_token")
    def _compute_share_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for rec in self:
            rec.share_url = f"{base}/custom_dashboard/share/{rec.share_token}" if rec.share_token else ""

    # ---------- workflow ----------

    def action_publish(self):
        self.write({"is_published": True})

    def action_unpublish(self):
        self.write({"is_published": False})

    def action_refresh_all_tiles(self):
        """Force-refresh every tile on this dashboard."""
        for rec in self:
            rec.tile_ids.action_refresh()
        return True

    def action_generate_share_link(self):
        for rec in self:
            rec.write({"share_token": secrets.token_urlsafe(32)})
        return True

    def action_revoke_share_link(self):
        for rec in self:
            rec.write({"share_token": False})
        return True

    def action_open_tile_records(self, tile_id: int | None = None):
        """Drill-down delegate so the dashboard form can dispatch to a tile."""
        self.ensure_one()
        if not tile_id:
            raise UserError(_("No tile selected for drill-down."))
        tile = self.tile_ids.filtered(lambda t: t.id == tile_id)
        if not tile:
            raise UserError(_("Tile not found on this dashboard."))
        return tile.action_open_tile_records()

    # ---------- AI: Ask question against this dashboard ----------

    def _custom_ai_payload(self, question: str) -> dict[str, Any]:
        self.ensure_one()
        tiles = []
        for tile in self.tile_ids:
            tiles.append(
                {
                    "name": tile.name,
                    "tile_type": tile.tile_type,
                    "model_name": tile.model_name or "",
                    "domain": tile.domain or "[]",
                    "measure_field": tile.measure_field or "",
                    "groupby_field": tile.groupby_field or "",
                    "cached_value": tile.cached_value or "",
                }
            )
        return {
            "dashboard": self.name,
            "description": (self.description or "")[:2000],
            "owner": self.owner_id.login or "",
            "tile_count": len(tiles),
            "tiles": tiles,
            "question": question,
        }

    def action_ask_ai(self, question: str | None = None):
        """Send the dashboard context + a user question to the AI gateway."""
        self.ensure_one()
        question = (question or self.last_ai_question or "").strip()
        if not question:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Ask AI"),
                    "message": _("Type a question first."),
                    "type": "warning",
                },
            }
        try:
            result = self.env["custom.ai"]._recommend(
                model="custom.dashboard",
                res_id=self.id,
                payload=self._custom_ai_payload(question),
            )
        except Exception as e:
            _logger.error("Dashboard Ask AI failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = result.get("response") or result.get("text") or result.get("summary") or json.dumps(result)[:1000]
        self.write(
            {
                "last_ai_question": question,
                "last_ai_answer": text,
                "last_ai_at": fields.Datetime.now(),
            }
        )
        self.message_post(
            body=_("<b>Ask AI:</b> %(q)s<br/><b>Answer:</b><br/>%(a)s")
            % {
                "q": question,
                "a": text,
            },
            subtype_xmlid="mail.mt_note",
        )
        return True
