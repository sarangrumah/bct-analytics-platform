# -*- coding: utf-8 -*-
"""Shared setup for the F&B stock-ops tests.

Reuses the connector's ``MockEsbTransport`` so nothing here needs ESB
credentials either. Every test class starts from a synced master set: two
branches, two locations, two products with their ESB product details, and two
purposes flagged as the gain/loss defaults.
"""

from __future__ import annotations

from datetime import timedelta

from odoo import fields

from odoo.addons.custom_esb_connector.tests.common import EsbTestCase, load_fixture


class FnbTestCase(EsbTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.param.set_param("esb.push_enabled", "1")
        # Approving a count line is supervisor-gated; the tests exercise the real
        # approval path rather than writing the status directly.
        cls.env.user.group_ids |= cls.env.ref("custom_wms_cycle_count.group_cycle_count_supervisor")

    def setUp(self):
        super().setUp()
        self._sync_masters()
        self.branch = self.env["custom.esb.branch"].search([("code", "=", "WRB")])
        self.hub = self.env["custom.esb.branch"].search([("code", "=", "HOF")])
        self.kitchen = self.branch.location_ids.filtered(lambda l: l.esb_location_id == 964)
        self.chiller = self.branch.location_ids.filtered(lambda l: l.esb_location_id == 967)
        self.ayam = self.env["product.product"].search([("x_esb_product_id", "=", 1088)])
        self.beras = self.env["product.product"].search([("x_esb_product_id", "=", 1090)])
        self._flag_purposes()

    def _sync_masters(self):
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("branch_list"))
        self.transport.register("GET", "/location", load_fixture("location_list"))
        self.transport.register("GET", "/purpose", load_fixture("purpose_list"))
        self.transport.register("GET", "/document-template", load_fixture("document_template_list"))
        self.transport.register("GET", "/supplier", load_fixture("supplier_list"))
        self.transport.register("GET", "/corev1/master/product", load_fixture("product_master"))
        self.env["custom.esb.master.sync"].action_sync_now()

    def _flag_purposes(self):
        Purpose = self.env["custom.esb.purpose"]
        Purpose.search([("esb_purpose_id", "=", 10)]).is_default_gain = True
        Purpose.search([("esb_purpose_id", "=", 9)]).is_default_loss = True

    # -- fixtures for the domain -------------------------------------

    def given_snapshot(self, location, product, qty, unit_value=45.0, as_of=None):
        Snapshot = self.env["custom.esb.stock.snapshot"]
        existing = Snapshot.search([("location_id", "=", location.id), ("product_id", "=", product.id)], limit=1)
        vals = {
            "branch_id": location.branch_id.id,
            "location_id": location.id,
            "product_id": product.id,
            "esb_product_detail_id": product.x_esb_product_detail_id,
            "qty": qty,
            "unit_value": unit_value,
            "as_of": as_of or fields.Datetime.now(),
        }
        if existing:
            existing.write(vals)
            return existing
        return Snapshot.create(vals)

    def given_demand(self, branch, product, pattern, end=None):
        """Seed daily history from a list of quantities, oldest first.

        ``end`` defaults to yesterday, matching what ``series()`` reads.
        """
        History = self.env["custom.fnb.demand.history"]
        end = end or fields.Date.context_today(self) - timedelta(days=1)
        start = end - timedelta(days=len(pattern) - 1)
        History.create(
            [
                {
                    "branch_id": branch.id,
                    "product_id": product.id,
                    "date": start + timedelta(days=i),
                    "qty": qty,
                }
                for i, qty in enumerate(pattern)
            ]
        )

    def given_forecast(self, branch, product, method="moving_average"):
        Forecast = self.env["custom.fnb.demand.forecast"]
        forecast = Forecast.create({"branch_id": branch.id, "product_id": product.id, "method": method})
        forecast._recompute_one()
        return forecast

    def given_session(self, location=None):
        location = location or self.kitchen
        location.action_create_odoo_location()
        return self.env["custom.cycle.count.session"].create(
            {
                "esb_branch_id": location.branch_id.id,
                "esb_location_id": location.id,
                "company_id": self.env.company.id,
            }
        )

    def count_line(self, session, product, counted_qty):
        """Count a line and drive it through the real supervisor approval gate."""
        line = session.line_ids.filtered(lambda l: l.product_id == product)
        line.action_count(counted_qty)
        line.action_approve()
        return line

    def expect_item_journal(self):
        self.transport.register("GET", "/inventory/item-journal", load_fixture("item_journal_index_empty"))
        self.transport.register("POST", "/inventory/item-journal", load_fixture("item_journal_created"))
