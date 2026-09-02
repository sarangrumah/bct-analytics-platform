# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_CAPACITY_CEILING = {
    # metric -> "capacity" upper bound used to compute severity.
    "cpu": 100.0,  # percent
    "memory": 100.0,  # percent
    "disk": 100.0,  # percent
    "db_size": None,  # not bounded; severity based on growth rate only
}


class CapacityForecast(models.Model):
    _name = "custom.ops.capacity.forecast"
    _description = "Capacity Forecast"
    _order = "generated_at desc, tenant_id"

    tenant_id = fields.Many2one(
        "tenant.registry",
        required=True,
        ondelete="cascade",
        index=True,
    )
    metric = fields.Selection(
        [("cpu", "CPU"), ("memory", "Memory"), ("disk", "Disk"), ("db_size", "DB Size")],
        required=True,
        index=True,
    )
    current_value = fields.Float()
    forecast_30d = fields.Float()
    forecast_90d = fields.Float()
    forecast_365d = fields.Float()
    confidence_lower = fields.Float()
    confidence_upper = fields.Float()
    generated_at = fields.Datetime(default=fields.Datetime.now, index=True)
    recommended_action = fields.Char()
    severity = fields.Selection(
        [("info", "Info"), ("warn", "Warning"), ("critical", "Critical")],
        compute="_compute_severity",
        store=True,
    )

    # ------------------------------------------------------------------

    @api.depends("metric", "forecast_30d", "forecast_90d")
    def _compute_severity(self):
        for rec in self:
            ceiling = _CAPACITY_CEILING.get(rec.metric)
            if ceiling is None:
                rec.severity = "info"
                continue
            if rec.forecast_30d > ceiling * 0.9:
                rec.severity = "critical"
            elif rec.forecast_90d > ceiling * 0.8:
                rec.severity = "warn"
            else:
                rec.severity = "info"

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_regenerate(self) -> None:
        Tenant = self.env["tenant.registry"]
        tenants = Tenant.sudo().search([("state", "=", "active")])
        if not tenants:
            return
        url = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "custom_ops_monitor.predictor_url",
                "http://predictor:8000/forecast",
            )
        )
        for tenant in tenants:
            for metric in ("cpu", "memory", "disk", "db_size"):
                history = self._tenant_history(tenant, metric)
                if len(history) < 3:
                    continue
                forecast = self._call_predictor(url, metric, history)
                if forecast is None:
                    continue
                self.sudo().create(
                    {
                        "tenant_id": tenant.id,
                        "metric": metric,
                        "current_value": history[-1]["value"],
                        "forecast_30d": float(forecast.get("forecast_30d") or 0),
                        "forecast_90d": float(forecast.get("forecast_90d") or 0),
                        "forecast_365d": float(forecast.get("forecast_365d") or 0),
                        "confidence_lower": float(forecast.get("confidence_lower") or 0),
                        "confidence_upper": float(forecast.get("confidence_upper") or 0),
                        "recommended_action": forecast.get("recommended_action") or "",
                    }
                )

    def _tenant_history(self, tenant, metric: str) -> list[dict]:
        Health = self.env["custom.ops.tenant.health"].sudo()
        field_map = {
            "cpu": "cpu_pct",
            "memory": "memory_pct",
            "disk": "disk_pct",
            "db_size": "db_size_mb",
        }
        f = field_map[metric]
        snaps = Health.search(
            [("tenant_id", "=", tenant.id), ("snapshot_at", ">=", fields.Datetime.now() - timedelta(days=30))],
            order="snapshot_at asc",
        )
        return [{"ts": s.snapshot_at.isoformat() if s.snapshot_at else "", "value": getattr(s, f)} for s in snaps]

    @staticmethod
    def _call_predictor(url: str, metric: str, history: list[dict]) -> dict | None:
        # The URL comes from an ir.config_parameter an administrator sets, so it is
        # not attacker-supplied — but urlopen honours file://, ftp:// and data://
        # too, and a mistyped or migrated parameter that happens to name a local
        # path would read that file and hand it back as a prediction. Checking the
        # scheme costs nothing and removes the whole class.
        if not url.startswith(("http://", "https://")):
            _logger.warning("predictor URL %r is not http(s); refusing to call it", url)
            return None
        body = json.dumps({"metric": metric, "history": history}).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 - scheme checked immediately above
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError) as e:
            _logger.warning("predictor call failed: %s", e)
            return None
