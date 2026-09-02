# -*- coding: utf-8 -*-
"""Daily demand history per outlet and product.

The demand signal comes from ESB OMS
``/corev1/sales/get-daily-sales-material-usage``, which reports **material**
consumption already exploded through the bill of materials — i.e. how much
chicken was consumed, not how many chicken dishes were sold. That is exactly
the granularity replenishment needs, and it saves us reimplementing ESB's BOM.

The feed answers one ``salesDate`` per call, so a backfill loops over days. It
is idempotent per (branch, product, date), so re-running a day overwrites rather
than doubles.

Fallback: where OMS is unavailable, ``qtyOut`` from the Core stock-movement
report gives consumption too, though it mixes in transfers and wastage.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.custom_esb_connector.models.esb_adapter import ESB_COREV1, EsbApiError

_logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 90
#: ESB's own name for the unit the material usage should be reported in. Stock
#: unit keeps the history in the same unit as the snapshot, so the two can be
#: compared without a conversion.
USAGE_UNIT = "stockUnit"

SOURCES = [("oms_material_usage", "OMS Daily Material Usage"), ("movement_report", "Stock Movement (qtyOut)")]


class FnbDemandHistory(models.Model):
    _name = "custom.fnb.demand.history"
    _description = "F&B Daily Demand History"
    _order = "date desc, branch_id, product_id"
    _rec_name = "product_id"

    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)
    date = fields.Date(required=True, index=True)
    qty = fields.Float(digits=(20, 4), help="Material consumed that day, in the product's stock unit.")
    uom_name = fields.Char()
    source = fields.Selection(SOURCES, default="oms_material_usage")

    _branch_product_date_uniq = models.Constraint(
        "unique(branch_id, product_id, date)", "Only one demand row per branch/product/day."
    )

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    @api.model
    def _cron_pull_demand(self):
        """Pull yesterday's usage for every in-scope branch.

        Yesterday, not today: a trading day is only complete once it has closed,
        and a partial day would drag every average down.
        """
        sync = self.env["custom.esb.master.sync"]
        log = self.env["custom.esb.sync.log"]
        if not sync._enabled("fnb.demand_sync_enabled"):
            log._record("pull", "demand", "skipped", message="fnb.demand_sync_enabled is off")
            return False
        yesterday = fields.Date.context_today(self) - timedelta(days=1)
        branches = self.env["custom.esb.branch"].sudo().search(self.env["custom.esb.stock.snapshot"]._branch_domain())
        for branch in branches:
            self.pull_day(branch, yesterday)
        return True

    @api.model
    def pull_day(self, branch, day):
        """Fetch and upsert one trading day for one branch."""
        sync = self.env["custom.esb.master.sync"]
        log = self.env["custom.esb.sync.log"]
        adapter = sync._adapter(ESB_COREV1)
        if adapter is None:
            log._record("pull", "demand:%s" % branch.code, "skipped", message="No active esb_corev1 adapter config")
            return False
        t0 = time.time()
        try:
            rows = adapter.get_rows(
                "corev1/sales/get-daily-sales-material-usage",
                {
                    "salesDate": fields.Date.to_string(day),
                    "flagUnit": USAGE_UNIT,
                    "branchCode": branch.code,
                },
            )
        except EsbApiError as exc:
            log._record("pull", "demand:%s" % branch.code, "error", message=str(exc))
            return False
        stats = self._apply_usage_rows(branch, day, rows)
        log._record(
            "pull",
            "demand:%s" % branch.code,
            "ok",
            record_count=len(rows),
            duration_ms=int((time.time() - t0) * 1000),
            **stats,
        )
        return True

    @api.model
    def _apply_usage_rows(self, branch, day, rows):
        """Upsert usage rows. Split out so it is testable without a transport."""
        created = updated = 0
        by_code = {}
        for row in rows:
            code = row.get("productCode")
            if not code:
                continue
            qty = row.get("totalConversionQty")
            if qty is None:
                qty = row.get("totalQty") or 0.0
            # A product can appear more than once per day (multiple menus using
            # the same material); the feed is per menu-material, so sum them.
            entry = by_code.setdefault(code, {"qty": 0.0, "uom": row.get("unitConversion") or row.get("unit")})
            entry["qty"] += qty

        products = (
            self.env["product.product"]
            .sudo()
            .with_context(active_test=False)
            .search([("x_esb_product_code", "in", list(by_code))])
        )
        by_product_code = {p.x_esb_product_code: p for p in products}
        for code, entry in by_code.items():
            product = by_product_code.get(code)
            if not product:
                # Not mirrored yet; the next master sync will pick it up.
                continue
            vals = {
                "branch_id": branch.id,
                "product_id": product.id,
                "date": day,
                "qty": entry["qty"],
                "uom_name": entry["uom"],
                "source": "oms_material_usage",
            }
            existing = self.sudo().search(
                [("branch_id", "=", branch.id), ("product_id", "=", product.id), ("date", "=", day)], limit=1
            )
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.sudo().create(vals)
                created += 1
        return {"created_count": created, "updated_count": updated}

    @api.model
    def action_backfill(self, branch=None, days=None):
        """Walk backwards day by day to seed history for a new outlet."""
        days = days or int(
            self.env["ir.config_parameter"].sudo().get_param("fnb.demand_backfill_days", DEFAULT_BACKFILL_DAYS)
        )
        branches = branch or self.env["custom.esb.branch"].sudo().search([("active", "=", True)])
        today = fields.Date.context_today(self)
        for br in branches:
            for offset in range(1, days + 1):
                self.pull_day(br, today - timedelta(days=offset))
        return True

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    @api.model
    def series(self, branch, product, days, end=None):
        """Daily quantities for the last ``days``, oldest first, zero-filled.

        Zero-filling matters: a day with no consumption is a real zero and must
        pull the average down. Leaving the gap out would overstate demand for
        anything that does not sell every day.
        """
        end = end or fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        rows = self.sudo().search_read(
            [
                ("branch_id", "=", branch.id),
                ("product_id", "=", product.id),
                ("date", ">=", start),
                ("date", "<=", end),
            ],
            ["date", "qty"],
        )
        by_date = {r["date"]: r["qty"] for r in rows}
        return [(start + timedelta(days=i), by_date.get(start + timedelta(days=i), 0.0)) for i in range(days)]

    @api.model
    def has_history(self, branch, product):
        return bool(self.sudo().search_count([("branch_id", "=", branch.id), ("product_id", "=", product.id)]))
