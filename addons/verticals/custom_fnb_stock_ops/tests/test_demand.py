# -*- coding: utf-8 -*-
"""Demand history ingest from the ESB OMS material-usage feed."""

from __future__ import annotations

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import FnbTestCase


@tagged("post_install", "-at_install", "esb", "fnb")
class TestDemandHistory(FnbTestCase):
    def setUp(self):
        super().setUp()
        self.History = self.env["custom.fnb.demand.history"]
        self.day = fields.Date.context_today(self) - timedelta(days=1)

    def _usage(self, rows):
        return {
            "path": "…/corev1/sales/get-daily-sales-material-usage",
            "status": "ok",
            "code": "EC03100000",
            "message": "OK",
            "result": rows,
            "errors": None,
        }

    def test_usage_rows_become_daily_history(self):
        self.transport.register(
            "GET",
            "/get-daily-sales-material-usage",
            self._usage(
                [
                    {
                        "branchCode": "WRB",
                        "salesDate": str(self.day),
                        "productCode": "AYM-001",
                        "productName": "Ayam Utuh",
                        "totalQty": 2500,
                        "unit": "GR",
                        "totalConversionQty": 2500,
                        "unitConversion": "GR",
                    },
                ]
            ),
        )

        self.History.pull_day(self.branch, self.day)

        row = self.History.search([("branch_id", "=", self.branch.id), ("date", "=", self.day)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.product_id, self.ayam)
        self.assertEqual(row.qty, 2500)

    def test_same_material_across_menus_is_summed(self):
        """The feed is per menu-material, so one ingredient appears once per dish."""
        self.transport.register(
            "GET",
            "/get-daily-sales-material-usage",
            self._usage(
                [
                    {"branchCode": "WRB", "productCode": "AYM-001", "totalConversionQty": 1500, "unitConversion": "GR"},
                    {"branchCode": "WRB", "productCode": "AYM-001", "totalConversionQty": 1000, "unitConversion": "GR"},
                ]
            ),
        )

        self.History.pull_day(self.branch, self.day)

        row = self.History.search([("product_id", "=", self.ayam.id), ("date", "=", self.day)])
        self.assertEqual(row.qty, 2500, "two menus using the same material add up")

    def test_pulling_the_same_day_twice_overwrites_rather_than_doubles(self):
        self.transport.register(
            "GET",
            "/get-daily-sales-material-usage",
            self._usage([{"branchCode": "WRB", "productCode": "AYM-001", "totalConversionQty": 2500}]),
        )

        self.History.pull_day(self.branch, self.day)
        self.History.pull_day(self.branch, self.day)

        rows = self.History.search([("product_id", "=", self.ayam.id), ("date", "=", self.day)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.qty, 2500)

    def test_unmirrored_products_are_skipped(self):
        self.transport.register(
            "GET",
            "/get-daily-sales-material-usage",
            self._usage([{"branchCode": "WRB", "productCode": "NOT-IN-ODOO", "totalConversionQty": 10}]),
        )

        self.History.pull_day(self.branch, self.day)

        self.assertEqual(self.History.search_count([("date", "=", self.day)]), 0)

    def test_conversion_qty_is_preferred_over_raw_qty(self):
        """flagUnit=stockUnit means totalConversionQty is already in the unit the
        snapshot uses, so history and on-hand are directly comparable."""
        self.transport.register(
            "GET",
            "/get-daily-sales-material-usage",
            self._usage(
                [
                    {
                        "branchCode": "WRB",
                        "productCode": "AYM-001",
                        "totalQty": 2.5,
                        "unit": "KG",
                        "totalConversionQty": 2500,
                        "unitConversion": "GR",
                    }
                ]
            ),
        )

        self.History.pull_day(self.branch, self.day)

        self.assertEqual(self.History.search([("product_id", "=", self.ayam.id)]).qty, 2500)

    def test_the_request_asks_for_the_stock_unit(self):
        self.transport.register("GET", "/get-daily-sales-material-usage", self._usage([]))

        self.History.pull_day(self.branch, self.day)

        url = self.transport.calls_to("GET", "/get-daily-sales-material-usage")[0]["url"]
        self.assertIn("flagUnit=stockUnit", url)
        self.assertIn("branchCode=WRB", url)

    def test_cron_is_a_no_op_while_the_switch_is_off(self):
        self.param.set_param("fnb.demand_sync_enabled", "0")
        before = len(self.transport.calls)

        self.History._cron_pull_demand()

        self.assertEqual(len(self.transport.calls), before)

    def test_cron_pulls_yesterday_not_today(self):
        """A partial trading day would drag every average down."""
        self.param.set_param("fnb.demand_sync_enabled", "1")
        self.transport.register("GET", "/get-daily-sales-material-usage", self._usage([]))

        self.History._cron_pull_demand()

        url = self.transport.calls_to("GET", "/get-daily-sales-material-usage")[0]["url"]
        self.assertIn("salesDate=%s" % self.day, url)

    # -- series ------------------------------------------------------

    def test_series_zero_fills_missing_days(self):
        """A day with no consumption is a real zero and must pull the mean down."""
        self.given_demand(self.branch, self.ayam, [10, 0, 0, 20])

        series = self.History.series(self.branch, self.ayam, 4)

        self.assertEqual([qty for _d, qty in series], [10, 0, 0, 20])

    def test_series_is_oldest_first(self):
        self.given_demand(self.branch, self.ayam, [1, 2, 3])

        series = self.History.series(self.branch, self.ayam, 3)

        self.assertEqual([qty for _d, qty in series], [1, 2, 3])
        self.assertLess(series[0][0], series[-1][0])

    def test_series_of_a_product_with_no_history_is_all_zeros(self):
        series = self.History.series(self.branch, self.beras, 5)

        self.assertEqual([qty for _d, qty in series], [0.0] * 5)
        self.assertFalse(self.History.has_history(self.branch, self.beras))
