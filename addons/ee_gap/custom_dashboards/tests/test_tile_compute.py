# -*- coding: utf-8 -*-
import json

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTileCompute(TransactionCase):
    def setUp(self):
        super().setUp()
        self.dashboard = self.env["custom.dashboard"].create(
            {
                "name": "Test Dashboard",
            }
        )

    def _make_tile(self, **vals):
        base = {
            "dashboard_id": self.dashboard.id,
            "name": vals.pop("name", "Tile"),
            "model_name": "res.users",
            "domain": "[]",
        }
        base.update(vals)
        return self.env["custom.dashboard.tile"].create(base)

    def test_tile_compute_count(self):
        tile = self._make_tile(tile_type="count")
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("value", payload)
        self.assertGreater(payload["value"], 0)
        self.assertFalse(tile.last_error)

    def test_tile_compute_sum(self):
        tile = self._make_tile(
            tile_type="sum",
            model_name="res.users",
            measure_field="id",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("value", payload)
        self.assertGreater(payload["value"], 0)

    def test_tile_compute_avg(self):
        tile = self._make_tile(
            tile_type="avg",
            model_name="res.users",
            measure_field="id",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("value", payload)
        self.assertGreaterEqual(payload["value"], 0)

    def test_tile_compute_last_value(self):
        tile = self._make_tile(
            tile_type="last_value",
            model_name="res.users",
            measure_field="login",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("value", payload)

    def test_tile_compute_formula(self):
        tile = self._make_tile(
            tile_type="formula",
            model_name=False,
            formula_expression="1 + 2 + 3",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertEqual(payload["value"], 6)

    def test_tile_compute_chart_bar(self):
        tile = self._make_tile(
            tile_type="chart_bar",
            model_name="res.users",
            measure_field="id",
            groupby_field="active",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("labels", payload)
        self.assertIn("data", payload)
        self.assertEqual(len(payload["labels"]), len(payload["data"]))

    def test_tile_compute_chart_pie(self):
        tile = self._make_tile(
            tile_type="chart_pie",
            model_name="res.users",
            measure_field="id",
            groupby_field="active",
        )
        tile.action_refresh()
        payload = json.loads(tile.cached_value)
        self.assertIn("labels", payload)
        self.assertIn("data", payload)

    def test_drill_down(self):
        tile = self._make_tile(tile_type="count", domain="[('active','=',True)]")
        action = tile.action_open_tile_records()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "res.users")
        self.assertEqual(action["domain"], [("active", "=", True)])

    def test_refresh_all(self):
        self._make_tile(tile_type="count")
        self._make_tile(tile_type="count", name="Tile2")
        self.dashboard.action_refresh_all_tiles()
        for tile in self.dashboard.tile_ids:
            self.assertTrue(tile.cached_at)

    def test_cron_refreshes_stale(self):
        tile = self._make_tile(
            tile_type="count",
            refresh_interval_seconds=60,
        )
        # No cached_at yet → considered stale
        self.env["custom.dashboard.tile"]._cron_refresh_stale_tiles()
        tile.invalidate_recordset()
        self.assertTrue(tile.cached_at)

    def test_share_token_generation(self):
        self.dashboard.action_generate_share_link()
        self.assertTrue(self.dashboard.share_token)
        self.assertIn("/custom_dashboard/share/", self.dashboard.share_url)
        self.dashboard.action_revoke_share_link()
        self.assertFalse(self.dashboard.share_token)
