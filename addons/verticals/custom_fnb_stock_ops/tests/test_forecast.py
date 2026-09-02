# -*- coding: utf-8 -*-
"""Forecast baselines, tested against series with known answers."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import FnbTestCase


@tagged("post_install", "-at_install", "esb", "fnb")
class TestForecast(FnbTestCase):
    def setUp(self):
        super().setUp()
        self.Forecast = self.env["custom.fnb.demand.forecast"]

    def test_moving_average_is_the_plain_mean(self):
        self.given_demand(self.branch, self.ayam, [10, 20, 30, 40])

        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        self.assertAlmostEqual(forecast.daily_qty, 25.0)
        self.assertEqual(forecast.sample_days, 4)

    def test_weighted_average_leans_on_recent_days(self):
        """Same numbers, rising trend: the weighted mean must exceed the plain one."""
        self.given_demand(self.branch, self.ayam, [10, 20, 30, 40])

        plain = self.given_forecast(self.branch, self.ayam, "moving_average").daily_qty
        weighted_rec = self.Forecast.search([("product_id", "=", self.ayam.id)])
        weighted_rec.method = "weighted_ma"
        weighted_rec._recompute_one()

        self.assertGreater(weighted_rec.daily_qty, plain)
        self.assertAlmostEqual(weighted_rec.daily_qty, 30.0)  # (10+40+90+160)/10

    def test_day_of_week_seasonality_is_captured(self):
        """The reason seasonal_dow is the default: a Saturday is not a Tuesday."""
        # 28 days ending yesterday. Weekends 100, weekdays 10.
        end = fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=27)
        pattern = [100 if (start + timedelta(days=i)).weekday() >= 5 else 10 for i in range(28)]
        self.given_demand(self.branch, self.ayam, pattern)
        forecast = self.given_forecast(self.branch, self.ayam, "seasonal_dow")

        # Ask for the next Saturday and the next Tuesday explicitly.
        saturday = end + timedelta(days=(5 - end.weekday()) % 7 or 7)
        tuesday = end + timedelta(days=(1 - end.weekday()) % 7 or 7)
        series = self.env["custom.fnb.demand.history"].series(self.branch, self.ayam, 28)

        self.assertAlmostEqual(self.Forecast._predict("seasonal_dow", series, saturday), 100.0)
        self.assertAlmostEqual(self.Forecast._predict("seasonal_dow", series, tuesday), 10.0)
        self.assertAlmostEqual(
            self.Forecast._predict("moving_average", series, saturday), forecast._moving_average(series)
        )

    def test_horizon_sums_day_by_day_not_daily_times_days(self):
        """A 3-day cover starting Friday is not three average days."""
        end = fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=27)
        pattern = [100 if (start + timedelta(days=i)).weekday() >= 5 else 10 for i in range(28)]
        self.given_demand(self.branch, self.ayam, pattern)
        forecast = self.given_forecast(self.branch, self.ayam, "seasonal_dow")

        friday = end + timedelta(days=(4 - end.weekday()) % 7 or 7)
        # Friday(10) + Saturday(100) + Sunday(100)
        self.assertAlmostEqual(forecast.horizon_qty(3, start_date=friday), 210.0)

    def test_leading_zeros_before_the_first_sale_are_excluded(self):
        """Those are 'the product did not exist yet', not 'nobody bought it'."""
        self.given_demand(self.branch, self.ayam, [0, 0, 0, 0, 10, 10])

        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        self.assertEqual(forecast.sample_days, 2)
        self.assertAlmostEqual(forecast.daily_qty, 10.0, msg="the four pre-launch zeros must not halve the mean")

    def test_zeros_after_launch_still_count(self):
        self.given_demand(self.branch, self.ayam, [10, 0, 0, 10])

        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        self.assertAlmostEqual(forecast.daily_qty, 5.0, msg="a quiet day is real demand information")

    def test_safety_stock_grows_with_volatility_and_service_level(self):
        self.given_demand(self.branch, self.ayam, [10, 10, 10, 10, 10, 10, 10])
        steady = self.given_forecast(self.branch, self.ayam, "moving_average")
        self.assertAlmostEqual(steady.safety_stock(2, 95), 0.0, msg="no variability, no safety stock")

        self.env["custom.fnb.demand.history"].search([]).unlink()
        self.given_demand(self.branch, self.ayam, [0, 20, 0, 20, 0, 20, 0])
        volatile = self.env["custom.fnb.demand.forecast"].search([("product_id", "=", self.ayam.id)])
        volatile._recompute_one()

        self.assertGreater(volatile.safety_stock(2, 95), 0.0)
        self.assertGreater(
            volatile.safety_stock(2, 99), volatile.safety_stock(2, 90), "a higher service level buys more cover"
        )
        self.assertGreater(
            volatile.safety_stock(8, 95), volatile.safety_stock(2, 95), "a longer lead time needs more cover"
        )

    def test_safety_stock_scales_with_sqrt_of_lead_time(self):
        self.given_demand(self.branch, self.ayam, [0, 20, 0, 20, 0, 20, 0, 20])
        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        one_day = forecast.safety_stock(1, 95)
        four_days = forecast.safety_stock(4, 95)

        self.assertAlmostEqual(four_days, one_day * 2, places=4, msg="sqrt(4) = 2, not 4")

    def test_reliability_needs_two_weeks_of_history(self):
        self.given_demand(self.branch, self.ayam, [5] * 3)
        short = self.given_forecast(self.branch, self.ayam, "moving_average")
        self.assertFalse(short.reliable)

        self.env["custom.fnb.demand.history"].search([]).unlink()
        self.env["custom.fnb.demand.forecast"].search([]).unlink()
        self.given_demand(self.branch, self.ayam, [5] * 20)
        long = self.given_forecast(self.branch, self.ayam, "moving_average")
        self.assertTrue(long.reliable)

    def test_backtest_reports_zero_error_on_a_flat_series(self):
        self.given_demand(self.branch, self.ayam, [7] * 40)

        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        self.assertAlmostEqual(forecast.mape, 0.0, places=6)

    def test_backtest_distinguishes_perfect_from_unmeasurable(self):
        """0.0 means 'predicted exactly'; None means 'cannot tell'. Conflating
        them made method comparison throw away the best method."""
        self.given_demand(self.branch, self.ayam, [7] * 40)
        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")
        History = self.env["custom.fnb.demand.history"]

        long_series = History.series(self.branch, self.ayam, 40)
        self.assertEqual(forecast._backtest(long_series, "moving_average"), 0.0)
        self.assertIsNone(forecast._backtest(long_series[:5], "moving_average"), "too short to measure")

    def test_backtest_prefers_the_seasonal_method_on_seasonal_data(self):
        end = fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=55)
        pattern = [100 if (start + timedelta(days=i)).weekday() >= 5 else 10 for i in range(56)]
        self.given_demand(self.branch, self.ayam, pattern)
        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")
        series = self.env["custom.fnb.demand.history"].series(self.branch, self.ayam, 56)

        seasonal_error = forecast._backtest(series, "seasonal_dow")
        flat_error = forecast._backtest(series, "moving_average")

        self.assertLess(seasonal_error, flat_error)

    def test_compare_methods_adopts_the_best_one(self):
        end = fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=55)
        pattern = [100 if (start + timedelta(days=i)).weekday() >= 5 else 10 for i in range(56)]
        self.given_demand(self.branch, self.ayam, pattern)
        forecast = self.given_forecast(self.branch, self.ayam, "moving_average")

        forecast.action_compare_methods()

        self.assertEqual(forecast.method, "seasonal_dow")

    def test_ensure_forecasts_creates_one_row_per_branch_product(self):
        self.given_demand(self.branch, self.ayam, [5, 5, 5])
        self.given_demand(self.branch, self.beras, [1, 1, 1])
        self.given_demand(self.hub, self.ayam, [2, 2, 2])

        self.Forecast.ensure_forecasts()
        self.Forecast.ensure_forecasts()

        self.assertEqual(self.Forecast.search_count([]), 3, "idempotent: no duplicates on a second run")

    def test_cron_is_a_no_op_while_the_switch_is_off(self):
        self.param.set_param("fnb.forecast_enabled", "0")
        self.given_demand(self.branch, self.ayam, [5, 5, 5])

        self.Forecast._cron_recompute_all()

        self.assertEqual(self.Forecast.search_count([]), 0)

    def test_cron_creates_and_computes_forecasts(self):
        self.param.set_param("fnb.forecast_enabled", "1")
        self.given_demand(self.branch, self.ayam, [5, 5, 5])

        self.Forecast._cron_recompute_all()

        forecast = self.Forecast.search([("product_id", "=", self.ayam.id)])
        self.assertTrue(forecast.computed_at)
        self.assertAlmostEqual(forecast.daily_qty, 5.0)
