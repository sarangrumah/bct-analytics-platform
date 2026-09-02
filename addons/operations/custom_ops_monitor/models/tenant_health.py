# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from odoo import api, fields, models

from .prometheus_client import PrometheusClient, PrometheusError

_logger = logging.getLogger(__name__)


class TenantHealth(models.Model):
    _name = "custom.ops.tenant.health"
    _description = "Tenant Health Snapshot"
    _order = "snapshot_at desc, tenant_id"
    _rec_name = "tenant_id"

    tenant_id = fields.Many2one(
        "tenant.registry",
        required=True,
        ondelete="cascade",
        index=True,
    )
    snapshot_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    cpu_pct = fields.Float(default=0.0)
    memory_pct = fields.Float(default=0.0)
    memory_mb_used = fields.Integer(default=0)
    memory_mb_total = fields.Integer(default=0)
    disk_pct = fields.Float(default=0.0)
    disk_gb_used = fields.Integer(default=0)
    disk_gb_total = fields.Integer(default=0)
    request_rate_per_min = fields.Float(default=0.0)
    error_rate_pct = fields.Float(default=0.0)
    db_size_mb = fields.Integer(default=0)
    redis_hit_rate_pct = fields.Float(default=0.0)
    last_backup_at = fields.Datetime()
    backup_status = fields.Selection(
        [("ok", "OK"), ("stale", "Stale"), ("failed", "Failed")],
        default="ok",
    )
    health_score = fields.Integer(
        compute="_compute_health",
        store=True,
        help="0-100, higher is better.",
    )
    status = fields.Selection(
        [("green", "Green"), ("yellow", "Yellow"), ("red", "Red")],
        compute="_compute_health",
        store=True,
        index=True,
    )

    # ------------------------------------------------------------------

    @api.depends(
        "cpu_pct",
        "memory_pct",
        "disk_pct",
        "error_rate_pct",
        "backup_status",
    )
    def _compute_health(self):
        for rec in self:
            score = 100
            score -= max(0, rec.cpu_pct - 50) * 0.4
            score -= max(0, rec.memory_pct - 60) * 0.5
            score -= max(0, rec.disk_pct - 70) * 0.8
            score -= rec.error_rate_pct * 4
            if rec.backup_status == "stale":
                score -= 10
            elif rec.backup_status == "failed":
                score -= 25
            score = max(0, min(100, int(round(score))))
            rec.health_score = score
            if score >= 75:
                rec.status = "green"
            elif score >= 50:
                rec.status = "yellow"
            else:
                rec.status = "red"

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_collect_snapshots(self) -> None:
        Tenant = self.env["tenant.registry"]
        tenants = Tenant.sudo().search([("state", "=", "active")])
        if not tenants:
            return
        try:
            client = PrometheusClient.from_env(self.env)
        except Exception as e:  # pragma: no cover
            _logger.warning("prometheus client init failed: %s", e)
            return
        try:
            metrics = self._collect_metrics_bulk(client)
        except PrometheusError as e:
            _logger.warning("prometheus query failed: %s", e)
            return
        for tenant in tenants:
            db = tenant.db_name
            data = metrics.get(db, {})
            vals = {
                "tenant_id": tenant.id,
                "snapshot_at": fields.Datetime.now(),
                "cpu_pct": data.get("cpu_pct", 0.0),
                "memory_pct": data.get("memory_pct", 0.0),
                "memory_mb_used": int(data.get("memory_mb_used", 0)),
                "memory_mb_total": int(data.get("memory_mb_total", 0)),
                "disk_pct": data.get("disk_pct", 0.0),
                "disk_gb_used": int(data.get("disk_gb_used", 0)),
                "disk_gb_total": int(data.get("disk_gb_total", 0)),
                "request_rate_per_min": data.get("request_rate_per_min", 0.0),
                "error_rate_pct": data.get("error_rate_pct", 0.0),
                "db_size_mb": int(data.get("db_size_mb", 0)),
                "redis_hit_rate_pct": data.get("redis_hit_rate_pct", 0.0),
                "last_backup_at": tenant.last_backup_at,
                "backup_status": self._classify_backup(tenant.last_backup_at),
            }
            self.sudo().create(vals)

    @staticmethod
    def _classify_backup(last_backup_at) -> str:
        if not last_backup_at:
            return "failed"
        age = datetime.now() - (
            last_backup_at if isinstance(last_backup_at, datetime) else datetime.fromisoformat(str(last_backup_at))
        )
        if age > timedelta(hours=36):
            return "failed"
        if age > timedelta(hours=26):
            return "stale"
        return "ok"

    def _collect_metrics_bulk(self, client: PrometheusClient) -> dict[str, dict]:
        """Run a fixed set of PromQL queries that return values labelled by
        ``db`` (tenant db name)."""
        queries = {
            "cpu_pct": "avg by (db) (rate(odoo_cpu_seconds_total[5m])) * 100",
            "memory_pct": "avg by (db) (odoo_memory_usage_ratio) * 100",
            "memory_mb_used": "avg by (db) (odoo_memory_used_bytes / 1024 / 1024)",
            "memory_mb_total": "avg by (db) (odoo_memory_total_bytes / 1024 / 1024)",
            "disk_pct": 'avg by (db) (1 - (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})) * 100',
            "disk_gb_used": 'avg by (db) ((node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_avail_bytes{mountpoint="/"}) / 1024 / 1024 / 1024)',
            "disk_gb_total": 'avg by (db) (node_filesystem_size_bytes{mountpoint="/"} / 1024 / 1024 / 1024)',
            "request_rate_per_min": "sum by (db) (rate(odoo_http_requests_total[1m])) * 60",
            "error_rate_pct": 'sum by (db) (rate(odoo_http_requests_total{status=~"5.."}[5m])) / sum by (db) (rate(odoo_http_requests_total[5m])) * 100',
            "db_size_mb": "avg by (db) (pg_database_size_bytes / 1024 / 1024)",
            "redis_hit_rate_pct": "avg by (db) (redis_keyspace_hits_total / (redis_keyspace_hits_total + redis_keyspace_misses_total)) * 100",
        }
        out: dict[str, dict] = {}
        for key, promql in queries.items():
            try:
                result = client.query(promql)
            except PrometheusError as e:
                _logger.warning("query %s failed: %s", key, e)
                continue
            per_db = PrometheusClient.values_by_label(result, "db")
            for db, val in per_db.items():
                out.setdefault(db, {})[key] = val
        return out
