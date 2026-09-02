# -*- coding: utf-8 -*-
"""Single KPI tile on a custom.dashboard.

Tiles compute a value (count/sum/avg/last_value/formula/chart_bar/chart_pie)
from any model + domain. The result is cached on the record as JSON text and
the UI shows that cached value with a Refresh button.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

TILE_TYPES = [
    ("count", "Count"),
    ("sum", "Sum"),
    ("avg", "Average"),
    ("last_value", "Last Value"),
    ("formula", "Formula"),
    ("chart_bar", "Bar Chart"),
    ("chart_pie", "Pie Chart"),
]


class CustomDashboardTile(models.Model):
    _name = "custom.dashboard.tile"
    _description = "Custom Dashboard Tile"
    _order = "dashboard_id, sequence, id"

    dashboard_id = fields.Many2one(
        "custom.dashboard",
        string="Dashboard",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    tile_type = fields.Selection(
        TILE_TYPES,
        default="count",
        required=True,
    )
    model_name = fields.Char(
        string="Model",
        help="Technical model name, e.g. helpdesk.ticket",
    )
    domain = fields.Char(
        default="[]",
        help="Odoo domain expression, e.g. [('state','=','open')]",
    )
    measure_field = fields.Char(
        help="Field to aggregate (sum/avg/last_value). Ignored for count.",
    )
    groupby_field = fields.Char(
        help="Group-by field used by chart tile types (chart_bar/chart_pie).",
    )
    formula_expression = fields.Text(
        help="Python expression for formula tile_type. Context: env, model, "
        "domain, fields. Example: env['sale.order'].search_count(domain)",
    )
    refresh_interval_seconds = fields.Integer(
        default=300,
        help="Cron will refresh the tile when older than this many seconds.",
    )
    color = fields.Char(default="#1f77b4")
    cached_value = fields.Text(
        readonly=True,
        help="Cached result as JSON text. Scalar tiles store {'value':..} and "
        "chart tiles store {'labels':[..], 'data':[..]}.",
    )
    cached_display = fields.Char(
        compute="_compute_cached_display",
        help="Human-readable cached value extracted from cached_value JSON.",
    )
    cached_at = fields.Datetime(readonly=True)
    last_error = fields.Char(readonly=True)

    # ---------- compute display ----------

    @api.depends("cached_value", "tile_type")
    def _compute_cached_display(self):
        for tile in self:
            payload = tile._load_cached_payload()
            if not payload:
                tile.cached_display = ""
                continue
            if tile.tile_type in ("chart_bar", "chart_pie"):
                labels = payload.get("labels") or []
                tile.cached_display = _("%d series") % len(labels)
            else:
                value = payload.get("value")
                tile.cached_display = "" if value is None else str(value)

    def _load_cached_payload(self) -> dict:
        self.ensure_one()
        if not self.cached_value:
            return {}
        try:
            data = json.loads(self.cached_value)
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    # ---------- domain helpers ----------

    def _eval_domain(self) -> list:
        self.ensure_one()
        raw = (self.domain or "[]").strip() or "[]"
        try:
            value = safe_eval(raw, {})
        except Exception as e:
            raise UserError(
                _("Invalid domain on tile %(name)s: %(err)s")
                % {
                    "name": self.name,
                    "err": e,
                }
            ) from e
        if not isinstance(value, list):
            raise UserError(_("Domain must evaluate to a list on tile %s") % self.name)
        return value

    # ---------- per-type compute helpers ----------

    def _compute_count(self, Model, domain):
        return {"value": Model.search_count(domain)}

    def _compute_sum(self, Model, domain):
        if not self.measure_field:
            raise UserError(_("Tile %s requires a measure_field for sum.") % self.name)
        rows = Model.search_read(domain, [self.measure_field])
        total = sum((r.get(self.measure_field) or 0) for r in rows)
        return {"value": total}

    def _compute_avg(self, Model, domain):
        if not self.measure_field:
            raise UserError(_("Tile %s requires a measure_field for avg.") % self.name)
        rows = Model.search_read(domain, [self.measure_field])
        nums = [(r.get(self.measure_field) or 0) for r in rows]
        avg = (sum(nums) / len(nums)) if nums else 0
        return {"value": avg}

    def _compute_last_value(self, Model, domain):
        if not self.measure_field:
            raise UserError(_("Tile %s requires a measure_field for last_value.") % self.name)
        rec = Model.search(domain, order="id desc", limit=1)
        if not rec:
            return {"value": None}
        return {"value": rec[self.measure_field]}

    def _compute_formula(self):
        expr = (self.formula_expression or "").strip()
        if not expr:
            raise UserError(_("Tile %s requires a formula_expression.") % self.name)
        ctx = {
            "env": self.env,
            "domain": self._eval_domain(),
            "model": self.model_name,
            "fields": fields,
        }
        try:
            value = safe_eval(expr, ctx)
        except Exception as e:
            raise UserError(
                _("Formula error on tile %(name)s: %(err)s")
                % {
                    "name": self.name,
                    "err": e,
                }
            ) from e
        return {"value": value}

    def _compute_chart(self, Model, domain):
        if not self.measure_field:
            raise UserError(_("Tile %s requires a measure_field for chart.") % self.name)
        if not self.groupby_field:
            raise UserError(_("Tile %s requires a groupby_field for chart.") % self.name)
        groups = Model.read_group(
            domain,
            fields=[self.measure_field],
            groupby=[self.groupby_field],
            lazy=False,
        )
        labels, data = [], []
        for g in groups:
            label_val = g.get(self.groupby_field)
            if isinstance(label_val, (list, tuple)) and len(label_val) >= 2:
                labels.append(str(label_val[1]))
            else:
                labels.append("" if label_val in (None, False) else str(label_val))
            data.append(g.get(self.measure_field) or 0)
        return {"labels": labels, "data": data}

    # ---------- main refresh ----------

    def action_refresh(self):
        """Recompute cached_value for each tile (all tile_type variants)."""
        now = fields.Datetime.now()
        for tile in self:
            payload, err = None, False
            if not tile.model_name and tile.tile_type != "formula":
                tile.write(
                    {
                        "cached_value": json.dumps({"value": 0}),
                        "cached_at": now,
                        "last_error": False,
                    }
                )
                continue
            if tile.model_name and tile.model_name not in self.env:
                _logger.warning(
                    "Dashboard tile %s references unknown model %s",
                    tile.name,
                    tile.model_name,
                )
                tile.write(
                    {
                        "cached_value": json.dumps({"value": None}),
                        "cached_at": now,
                        "last_error": _("Unknown model: %s") % tile.model_name,
                    }
                )
                continue
            try:
                Model = self.env[tile.model_name].sudo() if tile.model_name else None
                domain = tile._eval_domain() if tile.model_name else []
                if tile.tile_type == "count":
                    payload = tile._compute_count(Model, domain)
                elif tile.tile_type == "sum":
                    payload = tile._compute_sum(Model, domain)
                elif tile.tile_type == "avg":
                    payload = tile._compute_avg(Model, domain)
                elif tile.tile_type == "last_value":
                    payload = tile._compute_last_value(Model, domain)
                elif tile.tile_type == "formula":
                    payload = tile._compute_formula()
                elif tile.tile_type in ("chart_bar", "chart_pie"):
                    payload = tile._compute_chart(Model, domain)
                else:
                    payload = {"value": 0}
            except UserError as e:
                err = str(e)
                payload = {"value": None}
            except Exception as e:
                _logger.warning(
                    "Dashboard tile refresh failed (tile=%s, model=%s): %s",
                    tile.name,
                    tile.model_name,
                    e,
                )
                err = str(e)
                payload = {"value": None}
            tile.write(
                {
                    "cached_value": json.dumps(payload, default=str),
                    "cached_at": now,
                    "last_error": err or False,
                }
            )
        return True

    # ---------- cron ----------

    @api.model
    def _cron_refresh_stale_tiles(self):
        """Cron entry: refresh tiles whose cache exceeds their interval."""
        now = fields.Datetime.now()
        tiles = self.search([])
        stale = self.browse()
        for tile in tiles:
            interval = max(tile.refresh_interval_seconds or 300, 30)
            if not tile.cached_at or (now - tile.cached_at) >= timedelta(seconds=interval):
                stale |= tile
        if stale:
            _logger.info("Refreshing %d stale dashboard tiles", len(stale))
            stale.action_refresh()
        return True

    # ---------- drill-down ----------

    def action_open_tile_records(self):
        """Return an act_window for the underlying tile records."""
        self.ensure_one()
        if not self.model_name or self.model_name not in self.env:
            raise UserError(_("Tile has no resolvable model."))
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": self.model_name,
            "view_mode": "list,form",
            "domain": self._eval_domain(),
            "target": "current",
        }

    # ---------- onchange UX helpers ----------

    @api.onchange("model_name")
    def _onchange_model_name(self):
        if self.model_name and self.model_name not in self.env:
            return {
                "warning": {
                    "title": _("Unknown model"),
                    "message": _("Model %s is not installed.") % self.model_name,
                }
            }
