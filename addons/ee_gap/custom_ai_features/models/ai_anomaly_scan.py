# -*- coding: utf-8 -*-
"""Scheduled anomaly scanner — runs nightly, fans out to per-model scanners."""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


# Per-model scan configuration. Each tuple:
#   (model, metric_field, history_field_or_callable, history_days, min_history)
# Set as a registry so adding a new model is one-liner.
SCANNERS = [
    {
        "model": "account.move",
        "metric": "amount_total",
        "filter_domain": [("state", "=", "posted"), ("move_type", "=", "in_invoice")],
        "history_days": 180,
        "min_history": 5,
    },
    {
        "model": "hr.payslip",
        "metric": "take_home_pay",
        "filter_domain": [("state", "in", ("approved", "paid"))],
        "history_days": 365,
        "min_history": 3,
    },
    {
        "model": "custom.coretax.transaction",
        "metric": "retry_count",
        "filter_domain": [],
        "history_days": 30,
        "min_history": 5,
    },
]


class AiAnomalyScan(models.Model):
    _name = "ai.anomaly.scan"
    _description = "AI Anomaly Scan Run"
    _order = "started_at desc"

    name = fields.Char(default="New", readonly=True)
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    finished_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [("running", "Running"), ("done", "Done"), ("error", "Error")],
        default="running",
        required=True,
    )
    finding_ids = fields.One2many("ai.anomaly.finding", "scan_id")
    finding_count = fields.Integer(compute="_compute_finding_count")
    error = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _compute_finding_count(self):
        for rec in self:
            rec.finding_count = len(rec.finding_ids)

    # -----------------------------------------------------------------

    @api.model
    def _cron_run(self):
        scan = self.sudo().create({"name": f"Scan {fields.Datetime.now()}"})
        try:
            for cfg in SCANNERS:
                try:
                    scan._scan_model(cfg)
                except Exception:
                    _logger.exception("scan failed for %s", cfg["model"])
            scan.write({"state": "done", "finished_at": fields.Datetime.now()})
        except Exception as e:
            scan.write({"state": "error", "error": str(e), "finished_at": fields.Datetime.now()})

    def _scan_model(self, cfg: dict):
        self.ensure_one()
        Model = self.env.get(cfg["model"])
        if Model is None:
            return
        AI = self.env["custom.ai"].sudo()
        Finding = self.env["ai.anomaly.finding"].sudo()

        # Pull the most recent record + N historical points
        since = fields.Datetime.now() - timedelta(days=cfg["history_days"])
        records = Model.sudo().search(
            (cfg.get("filter_domain") or []) + [("create_date", ">=", since)],
            order="create_date desc",
            limit=200,
        )
        if len(records) < cfg["min_history"] + 1:
            return

        latest = records[0]
        history_values = []
        for r in records[1 : cfg["min_history"] + 50]:
            try:
                history_values.append(float(r[cfg["metric"]]))
            except Exception:
                continue
        if len(history_values) < cfg["min_history"]:
            return

        try:
            latest_val = float(latest[cfg["metric"]])
        except Exception:
            return

        try:
            result = AI._detect_anomaly(
                model=cfg["model"],
                res_id=latest.id,
                metric=cfg["metric"],
                latest_value=latest_val,
                history=history_values,
                context={
                    "filter_domain": cfg.get("filter_domain"),
                    "history_days": cfg["history_days"],
                },
            )
        except Exception as e:
            _logger.warning("AI anomaly check failed for %s/%s: %s", cfg["model"], latest.id, e)
            return

        # Only create a finding when AI flags it as an anomaly with non-trivial confidence
        if not result.get("is_anomaly") or result.get("score", 0) < 0.5:
            return

        Finding.create(
            {
                "scan_id": self.id,
                "res_model": cfg["model"],
                "res_id": latest.id,
                "metric": cfg["metric"],
                "latest_value": latest_val,
                "severity": result.get("severity", "info"),
                "score": result.get("score", 0.0),
                "rationale": result.get("rationale", ""),
                "suggested_action": result.get("suggested_action", ""),
            }
        )
