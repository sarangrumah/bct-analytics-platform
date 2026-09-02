# -*- coding: utf-8 -*-
"""Per-tenant per-day AI usage aggregates.

Populated by ``_cron_refresh`` which pulls raw usage rows from
``custom_ai_bridge`` / ``custom_ai_features`` (when present) and rolls
them up by ``(tenant_id, date, model_name)``. The aggregate is what the
Hub dashboard reads — it's cheap to query and pivot-friendly.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CustomHubAiUsage(models.Model):
    _name = "custom.hub.ai.usage"
    _description = "Hub AI Usage Aggregate"
    _order = "date desc, tenant_id, model_name"
    _rec_name = "model_name"

    tenant_id = fields.Many2one("tenant.registry", string="Tenant", ondelete="cascade", index=True)
    date = fields.Date(required=True, index=True)
    model_name = fields.Char(required=True, index=True)
    input_tokens = fields.Integer(default=0)
    output_tokens = fields.Integer(default=0)
    cache_read_tokens = fields.Integer(default=0)
    cache_creation_tokens = fields.Integer(default=0)
    cost_usd = fields.Float(digits=(12, 4), default=0.0)
    request_count = fields.Integer(default=0)
    cache_hit_rate_pct = fields.Float(
        string="Cache Hit Rate (%)",
        compute="_compute_cache_hit_rate",
        store=True,
        digits=(5, 2),
    )

    _tenant_date_model_uniq = models.Constraint(
        "unique(tenant_id, date, model_name)",
        "An AI usage aggregate row must be unique per (tenant, date, model).",
    )

    @api.depends("input_tokens", "cache_read_tokens", "cache_creation_tokens")
    def _compute_cache_hit_rate(self):
        for rec in self:
            total_input = rec.input_tokens + rec.cache_read_tokens + rec.cache_creation_tokens
            rec.cache_hit_rate_pct = (rec.cache_read_tokens / total_input) * 100.0 if total_input else 0.0

    # ------------------------------------------------------------------
    # Cron / refresh
    # ------------------------------------------------------------------
    @api.model
    def _cron_refresh(self, lookback_days: int = 7) -> dict:
        """Pull raw rows from ``custom.ai`` (custom_ai_bridge) if installed,
        and roll up into this aggregate. Safe when ai_bridge is absent.
        Returns a stats dict."""
        if "custom.ai" not in self.env:
            _logger.info("[hub_ai_usage] custom_ai_bridge not installed; skip refresh")
            return {"rolled_up": 0, "skipped": True}

        Bridge = self.env["custom.ai"].sudo()
        # The bridge model exposes a usage_log table or similar; we try
        # a couple of common shapes — and if none are present, no-op.
        if not hasattr(Bridge, "_hub_usage_iter"):
            _logger.info("[hub_ai_usage] custom_ai has no _hub_usage_iter helper — no aggregation performed.")
            return {"rolled_up": 0, "skipped": True}

        cutoff = fields.Date.today() - timedelta(days=lookback_days)
        buckets: dict[tuple, dict] = defaultdict(
            lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "request_count": 0,
            }
        )
        for row in Bridge._hub_usage_iter(since=cutoff):
            key = (
                row.get("tenant_id") or False,
                row.get("date"),
                row.get("model_name") or "unknown",
            )
            b = buckets[key]
            b["input_tokens"] += int(row.get("input_tokens") or 0)
            b["output_tokens"] += int(row.get("output_tokens") or 0)
            b["cache_read_tokens"] += int(row.get("cache_read_tokens") or 0)
            b["cache_creation_tokens"] += int(row.get("cache_creation_tokens") or 0)
            b["cost_usd"] += float(row.get("cost_usd") or 0.0)
            b["request_count"] += int(row.get("request_count") or 1)

        rolled = 0
        for (tenant_id, date, model_name), vals in buckets.items():
            existing = self.search(
                [
                    ("tenant_id", "=", tenant_id or False),
                    ("date", "=", date),
                    ("model_name", "=", model_name),
                ],
                limit=1,
            )
            full = {
                **vals,
                "tenant_id": tenant_id or False,
                "date": date,
                "model_name": model_name,
            }
            if existing:
                existing.write(full)
            else:
                self.create(full)
            rolled += 1
        _logger.info("[hub_ai_usage] refresh complete: %s rows", rolled)
        return {"rolled_up": rolled, "skipped": False}
