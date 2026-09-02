# -*- coding: utf-8 -*-
"""Stock snapshot: reducing movement rows to closing balances.

The interesting logic is not the HTTP call but the reduction — picking the
chronologically last movement per (location, productDetail) out of an unsorted
report, and refusing to invent a zero for products that never moved.
"""

from __future__ import annotations

from odoo.tests import tagged

from .common import EsbTestCase, load_fixture


@tagged("post_install", "-at_install", "esb")
class TestEsbStockSnapshot(EsbTestCase):
    def setUp(self):
        super().setUp()
        self.sync = self.env["custom.esb.master.sync"]
        self.Snapshot = self.env["custom.esb.stock.snapshot"]
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("branch_list"))
        self.transport.register("GET", "/location", load_fixture("location_list"))
        self.transport.register("GET", "/purpose", load_fixture("purpose_list"))
        self.transport.register("GET", "/document-template", load_fixture("document_template_list"))
        self.transport.register("GET", "/corev1/master/product", load_fixture("product_master"))
        self.sync.action_sync_now()
        self.branch = self.env["custom.esb.branch"].search([("code", "=", "WRB")])
        self.kitchen = self.branch.location_ids.filtered(lambda location: location.esb_location_id == 964)
        self.ayam = self.env["product.product"].search([("x_esb_product_id", "=", 1088)])

    def test_snapshot_takes_the_latest_balance_not_the_first_row(self):
        """The report is not sorted; the fixture deliberately lists 20-Jul before
        19-Jul so a naive 'last row wins' would record the wrong balance."""
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.Snapshot.refresh_branch(self.branch)

        snap = self.Snapshot.search([("location_id", "=", self.kitchen.id), ("product_id", "=", self.ayam.id)])
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap.qty, 7500, "the 20-Jul balance, not the 18-Jul or 19-Jul one")
        self.assertEqual(snap.unit_value, 45)

    def test_snapshot_is_split_per_location(self):
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.Snapshot.refresh_branch(self.branch)

        snaps = self.Snapshot.search([("branch_id", "=", self.branch.id)])
        self.assertEqual(len(snaps), 2, "one row per (location, product) pair present in the report")
        self.assertEqual(set(snaps.mapped("location_id.name")), {"Kitchen SF WRB", "Chiller"})

    def test_refresh_is_idempotent(self):
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.Snapshot.refresh_branch(self.branch)
        self.Snapshot.refresh_branch(self.branch)

        self.assertEqual(self.Snapshot.search_count([("branch_id", "=", self.branch.id)]), 2)

    def test_unmoved_product_has_no_snapshot_and_qty_for_returns_none(self):
        """Critical: 'no movement' means unknown, never zero. Treating it as zero
        would post a fabricated adjustment into ESB's general ledger."""
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))
        self.Snapshot.refresh_branch(self.branch)
        beras = self.env["product.product"].search([("x_esb_product_id", "=", 1090)])

        qty = self.Snapshot.qty_for(self.kitchen, beras)

        self.assertIsNone(qty, "an unmoved product must report unknown, not 0.0")

    def test_qty_for_returns_the_balance_when_known(self):
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))
        self.Snapshot.refresh_branch(self.branch)

        self.assertEqual(self.Snapshot.qty_for(self.kitchen, self.ayam), 7500)

    def test_rows_for_unmirrored_products_are_skipped(self):
        """A SKU created in ESB since the last master sync must not invent a product."""
        payload = load_fixture("stock_movement")
        payload["result"]["data"][0]["productDetailID"] = 999999
        self.transport.register("GET", "/report/stock-movement", payload)

        self.Snapshot.refresh_branch(self.branch)

        self.assertFalse(self.Snapshot.search([("esb_product_detail_id", "=", 999999)]))

    def test_refresh_requests_the_configured_lookback_window(self):
        self.param.set_param("esb.snapshot_lookback_days", "30")
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.Snapshot.refresh_branch(self.branch)

        url = self.transport.calls_to("GET", "/report/stock-movement")[0]["url"]
        self.assertIn("branchCode=WRB", url)
        self.assertIn("startPeriod=", url)
        self.assertIn("endPeriod=", url)

    def test_cron_is_a_no_op_while_the_switch_is_off(self):
        self.param.set_param("esb.snapshot_enabled", "0")
        before = len(self.transport.calls)

        result = self.Snapshot._cron_refresh_snapshots()

        self.assertFalse(result)
        self.assertEqual(len(self.transport.calls), before)

    def test_branch_whitelist_narrows_the_cron(self):
        self.param.set_param("esb.snapshot_enabled", "1")
        self.param.set_param("esb.branch_whitelist", "WRB")
        self.transport.register("GET", "/report/stock-movement", load_fixture("stock_movement"))

        self.Snapshot._cron_refresh_snapshots()

        urls = [c["url"] for c in self.transport.calls_to("GET", "/report/stock-movement")]
        self.assertEqual(len(urls), 1, "only the whitelisted branch is polled")
        self.assertIn("branchCode=WRB", urls[0])
