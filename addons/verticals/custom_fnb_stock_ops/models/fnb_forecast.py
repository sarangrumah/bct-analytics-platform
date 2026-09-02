# -*- coding: utf-8 -*-
"""Demand forecast per outlet and product.

Deliberately simple and explainable. F&B replenishment decisions get argued
about by store managers, so a forecast they can reproduce on paper is worth more
than a marginally lower error they cannot. Three baselines:

- ``moving_average`` — mean of the last N days. Robust, no assumptions.
- ``weighted_ma`` — linearly weighted, recent days count more. Reacts to trend.
- ``seasonal_dow`` — mean of the same weekday over the last K weeks. **Default**,
  because F&B demand is dominated by day-of-week: a Saturday looks nothing like
  a Tuesday, and averaging them serves neither.

No ML dependency: pure Python, so the module installs anywhere. ``method`` is a
Selection, so a heavier model can be added later without touching consumers —
they only read ``daily_qty`` and ``safety_stock``.

Accuracy is measured by walk-forward backtest (:meth:`_backtest`) and stored as
MAPE, so a planner can see which method is actually working per product rather
than trusting the default.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

METHODS = [
    ("seasonal_dow", "Day-of-Week Seasonal"),
    ("weighted_ma", "Weighted Moving Average"),
    ("moving_average", "Moving Average"),
]

DEFAULT_WINDOW_DAYS = 56  # 8 whole weeks — 8 samples per weekday
MIN_DAYS_FOR_FORECAST = 14
#: Service level → z. Enough resolution for stock policy; a full inverse-normal
#: would imply a precision the demand data does not support.
Z_BY_SERVICE_LEVEL = {50: 0.0, 80: 0.84, 85: 1.04, 90: 1.28, 95: 1.65, 97: 1.88, 99: 2.33}


class FnbDemandForecast(models.Model):
    _name = "custom.fnb.demand.forecast"
    _description = "F&B Demand Forecast"
    _order = "branch_id, product_id"
    _rec_name = "product_id"

    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)

    method = fields.Selection(METHODS, default="seasonal_dow", required=True)
    window_days = fields.Integer(default=DEFAULT_WINDOW_DAYS)
    daily_qty = fields.Float(digits=(20, 4), readonly=True, help="Forecast average daily consumption.")
    demand_stdev = fields.Float(digits=(20, 4), readonly=True, help="Daily standard deviation over the window.")
    sample_days = fields.Integer(readonly=True, help="Days of history the forecast was computed from.")
    mape = fields.Float(
        string="MAPE %",
        readonly=True,
        help="Mean absolute percentage error from a walk-forward backtest. Lower is better; "
        "above ~50% the series is too erratic to plan from.",
    )
    computed_at = fields.Datetime(readonly=True)
    reliable = fields.Boolean(
        compute="_compute_reliable",
        store=True,
        help="Enough history to plan from. Unreliable forecasts still propose, but are flagged.",
    )

    _branch_product_uniq = models.Constraint("unique(branch_id, product_id)", "Only one forecast per branch/product.")

    @api.depends("sample_days", "daily_qty")
    def _compute_reliable(self):
        for rec in self:
            rec.reliable = rec.sample_days >= MIN_DAYS_FOR_FORECAST

    @api.depends("product_id", "branch_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.product_id.display_name} @ {rec.branch_id.display_name}"

    # ------------------------------------------------------------------
    # Forecast maths
    # ------------------------------------------------------------------

    @staticmethod
    def _moving_average(series):
        values = [qty for _date, qty in series]
        return statistics.fmean(values) if values else 0.0

    @staticmethod
    def _weighted_ma(series):
        """Linearly weighted: the most recent day carries the most weight."""
        values = [qty for _date, qty in series]
        if not values:
            return 0.0
        weights = range(1, len(values) + 1)
        total = sum(weights)
        return sum(v * w for v, w in zip(values, weights, strict=False)) / total

    @staticmethod
    def _seasonal_dow(series, target_date):
        """Mean of the same weekday. Falls back to the overall mean if that
        weekday never appears in the window (short history)."""
        values = [qty for date, qty in series if date.weekday() == target_date.weekday()]
        if values:
            return statistics.fmean(values)
        allv = [qty for _date, qty in series]
        return statistics.fmean(allv) if allv else 0.0

    @api.model
    def _predict(self, method, series, target_date):
        if method == "moving_average":
            return self._moving_average(series)
        if method == "weighted_ma":
            return self._weighted_ma(series)
        return self._seasonal_dow(series, target_date)

    @api.model
    def _predict_horizon(self, method, series, start_date, days):
        """Total expected consumption over ``days`` starting at ``start_date``.

        Day-by-day rather than ``daily x days`` because the seasonal method gives
        a different answer per weekday — a 3-day cover starting Friday is not
        three average days.
        """
        return sum(self._predict(method, series, start_date + timedelta(days=i)) for i in range(days))

    def _backtest(self, series, method, holdout=14):
        """Walk-forward MAPE: predict each of the last ``holdout`` days using
        only the days before it.

        Returns ``None`` when the error is not measurable — too little history,
        or a holdout with no non-zero day. ``None`` rather than ``0.0`` because
        a *perfect* forecast legitimately scores 0.0, and conflating the two
        would make method comparison discard exactly the best method.

        Days with zero actual demand are excluded from the percentage error —
        the ratio is undefined there and would otherwise dominate the mean.
        """
        if len(series) < MIN_DAYS_FOR_FORECAST + holdout:
            return None
        errors = []
        for i in range(len(series) - holdout, len(series)):
            date, actual = series[i]
            if not actual:
                continue
            predicted = self._predict(method, series[:i], date)
            errors.append(abs(actual - predicted) / abs(actual))
        return 100.0 * statistics.fmean(errors) if errors else None

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------

    def action_recompute(self):
        for rec in self:
            rec._recompute_one()
        return True

    def _recompute_one(self):
        self.ensure_one()
        History = self.env["custom.fnb.demand.history"]
        series = History.series(self.branch_id, self.product_id, self.window_days or DEFAULT_WINDOW_DAYS)
        values = [qty for _date, qty in series]
        # Leading zeros are usually "the product did not exist yet" rather than
        # "nobody bought it", so the sample starts at the first real movement.
        first_move = next((i for i, v in enumerate(values) if v), None)
        effective = series[first_move:] if first_move is not None else []
        tomorrow = fields.Date.context_today(self) + timedelta(days=1)
        self.write(
            {
                "daily_qty": self._predict(self.method, effective, tomorrow),
                "demand_stdev": statistics.stdev([q for _d, q in effective]) if len(effective) > 1 else 0.0,
                "sample_days": len(effective),
                "mape": self._backtest(effective, self.method) or 0.0,
                "computed_at": fields.Datetime.now(),
            }
        )
        return True

    def horizon_qty(self, days, start_date=None):
        """Expected consumption over the next ``days``."""
        self.ensure_one()
        History = self.env["custom.fnb.demand.history"]
        series = History.series(self.branch_id, self.product_id, self.window_days or DEFAULT_WINDOW_DAYS)
        start = start_date or (fields.Date.context_today(self) + timedelta(days=1))
        values = [qty for _date, qty in series]
        first_move = next((i for i, v in enumerate(values) if v), None)
        effective = series[first_move:] if first_move is not None else []
        return self._predict_horizon(self.method, effective, start, days)

    def safety_stock(self, lead_time_days, service_level=95):
        """Classic ``z * sigma * sqrt(lead time)``.

        Scaling by the square root of lead time, not lead time itself, because
        daily deviations partly cancel out over a longer cover.
        """
        self.ensure_one()
        z = Z_BY_SERVICE_LEVEL.get(int(service_level))
        if z is None:
            z = Z_BY_SERVICE_LEVEL[min(Z_BY_SERVICE_LEVEL, key=lambda k: abs(k - service_level))]
        return z * (self.demand_stdev or 0.0) * math.sqrt(max(1, lead_time_days))

    # ------------------------------------------------------------------
    # Bulk / cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_recompute_all(self):
        sync = self.env["custom.esb.master.sync"]
        if not sync._enabled("fnb.forecast_enabled"):
            self.env["custom.esb.sync.log"]._record(
                "pull", "forecast", "skipped", message="fnb.forecast_enabled is off"
            )
            return False
        self.ensure_forecasts()
        forecasts = self.sudo().search([])
        for forecast in forecasts:
            forecast._recompute_one()
        self.env["custom.esb.sync.log"]._record("pull", "forecast", "ok", record_count=len(forecasts))
        return True

    @api.model
    def ensure_forecasts(self):
        """Create a forecast row for every (branch, product) that has history."""
        self.env.cr.execute(
            """
            INSERT INTO custom_fnb_demand_forecast
                   (branch_id, product_id, method, window_days, create_uid, write_uid, create_date, write_date)
            SELECT DISTINCT h.branch_id, h.product_id, %s, %s, %s, %s, NOW(), NOW()
              FROM custom_fnb_demand_history h
             WHERE NOT EXISTS (
                   SELECT 1 FROM custom_fnb_demand_forecast f
                    WHERE f.branch_id = h.branch_id AND f.product_id = h.product_id)
            """,
            ("seasonal_dow", DEFAULT_WINDOW_DAYS, self.env.uid, self.env.uid),
        )
        self.invalidate_model()
        return True

    def action_compare_methods(self):
        """Backtest every method and adopt the most accurate one for this series."""
        History = self.env["custom.fnb.demand.history"]
        for rec in self:
            series = History.series(rec.branch_id, rec.product_id, rec.window_days or DEFAULT_WINDOW_DAYS)
            scored = [(rec._backtest(series, method), method) for method, _label in METHODS]
            # Keep 0.0 (a perfect forecast); drop only None (not measurable).
            scored = [s for s in scored if s[0] is not None]
            if not scored:
                raise UserError(_("Not enough history for %s to compare methods.") % rec.display_name)
            rec.method = min(scored)[1]
            rec._recompute_one()
        return True
