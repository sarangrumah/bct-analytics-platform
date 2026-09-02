# -*- coding: utf-8 -*-
"""Master sync: idempotent upsert, productDetail mapping, graceful no-op."""

from __future__ import annotations

from odoo.tests import tagged

from .common import EsbTestCase, load_fixture


@tagged("post_install", "-at_install", "esb")
class TestEsbMasterSync(EsbTestCase):
    def setUp(self):
        super().setUp()
        self.sync = self.env["custom.esb.master.sync"]

    def _register_all_feeds(self):
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("branch_list"))
        self.transport.register("GET", "/location", load_fixture("location_list"))
        self.transport.register("GET", "/purpose", load_fixture("purpose_list"))
        self.transport.register("GET", "/document-template", load_fixture("document_template_list"))
        self.transport.register("GET", "/corev1/master/product", load_fixture("product_master"))

    def test_full_sync_creates_all_master_records(self):
        self._register_all_feeds()

        self.sync.action_sync_now()

        self.assertEqual(self.env["custom.esb.branch"].search_count([]), 2)
        self.assertEqual(self.env["custom.esb.purpose"].search_count([]), 2)
        self.assertEqual(self.env["custom.esb.document.template"].search_count([]), 1)
        self.assertEqual(self.env["product.product"].search_count([("x_esb_product_id", "!=", 0)]), 2)

    def test_sync_is_idempotent(self):
        """Running twice must change counts by zero — this is what makes a cron safe."""
        self._register_all_feeds()
        self.sync.action_sync_now()
        before = {
            model: self.env[model].search_count([])
            for model in ("custom.esb.branch", "custom.esb.location", "custom.esb.purpose", "custom.esb.product.detail")
        }

        self.sync.action_sync_now()

        for model, count in before.items():
            self.assertEqual(self.env[model].search_count([]), count, f"{model} duplicated on re-sync")

    def test_locations_are_pulled_per_branch(self):
        self._register_all_feeds()

        self.sync.action_sync_now()

        wrb = self.env["custom.esb.branch"].search([("code", "=", "WRB")])
        self.assertEqual(len(wrb.location_ids), 2)
        self.assertEqual(set(wrb.location_ids.mapped("esb_location_id")), {964, 967})

    def test_stock_unit_detail_is_denormalised_onto_the_product(self):
        """productDetailID — not productID — is the key ESB transactions need."""
        self._register_all_feeds()

        self.sync.action_sync_now()

        ayam = self.env["product.product"].search([("x_esb_product_id", "=", 1088)])
        self.assertEqual(ayam.x_esb_product_detail_id, 2112, "the stock-unit detail is the default")
        self.assertEqual(len(ayam.x_esb_detail_ids), 2, "both units are mirrored")

    def test_purchase_unit_detail_is_resolvable(self):
        self._register_all_feeds()
        self.sync.action_sync_now()
        ayam = self.env["product.product"].search([("x_esb_product_id", "=", 1088)])

        self.assertEqual(ayam._esb_detail_id("stock"), 2112)
        self.assertEqual(ayam._esb_detail_id("purchase"), 2113, "purchases go out in the purchase unit")

    def test_detail_lookup_falls_back_when_unit_not_declared(self):
        """ESB does not guarantee every default unit is set on every product."""
        self._register_all_feeds()
        self.sync.action_sync_now()
        beras = self.env["product.product"].search([("x_esb_product_id", "=", 1090)])

        self.assertEqual(beras._esb_detail_id("transfer"), 2058, "single-unit product resolves for any kind")

    def test_existing_product_name_is_not_overwritten(self):
        """A name curated in Odoo must survive the nightly sync."""
        self._register_all_feeds()
        self.sync.action_sync_now()
        ayam = self.env["product.product"].search([("x_esb_product_id", "=", 1088)])
        ayam.name = "Ayam Utuh (Grade A)"

        self.sync.action_sync_now()

        self.assertEqual(ayam.name, "Ayam Utuh (Grade A)")

    def test_branch_missing_from_esb_is_archived_not_deleted(self):
        """Snapshots and history point at branches; deleting would orphan them."""
        self._register_all_feeds()
        self.sync.action_sync_now()
        shrunk = load_fixture("branch_list")
        shrunk["result"] = shrunk["result"][:1]
        self.transport.routes.clear()
        self.given_logged_in()
        self.transport.register("GET", "/branch", shrunk)

        self.sync._run_feed("branch", "esb_core", "_upsert_branches")

        wrb = self.env["custom.esb.branch"].with_context(active_test=False).search([("code", "=", "WRB")])
        self.assertTrue(wrb, "the branch record still exists")
        self.assertFalse(wrb.active, "but is archived")

    def test_cron_is_a_no_op_while_the_switch_is_off(self):
        self.param.set_param("esb.master_sync_enabled", "0")

        result = self.env["custom.esb.master.sync"]._cron_sync_masters()

        self.assertFalse(result)
        self.assertEqual(self.transport.calls, [], "nothing may be called while the feature is off")
        log = self.env["custom.esb.sync.log"].search([("operation", "=", "master")], limit=1)
        self.assertEqual(log.status, "skipped")

    def test_disabled_adapter_config_skips_rather_than_crashes(self):
        """The module must be installable long before ESB credentials exist."""
        self.core_config.status = "disabled"
        self.corev1_config.status = "disabled"

        self.env["custom.esb.master.sync"].action_sync_now()

        self.assertEqual(self.transport.calls, [])
        logs = self.env["custom.esb.sync.log"].search([("operation", "like", "master:%")])
        self.assertTrue(logs)
        self.assertTrue(all(log.status == "skipped" for log in logs))

    def test_feed_error_is_logged_and_does_not_abort_other_feeds(self):
        self.given_logged_in()
        self.transport.register("GET", "/branch", load_fixture("validation_error"))
        self.transport.register("GET", "/purpose", load_fixture("purpose_list"))
        self.transport.register("GET", "/document-template", load_fixture("document_template_list"))
        self.transport.register("GET", "/corev1/master/product", load_fixture("product_master"))

        self.sync.action_sync_now()

        branch_log = self.env["custom.esb.sync.log"].search([("operation", "=", "master:branch")], limit=1)
        purpose_log = self.env["custom.esb.sync.log"].search([("operation", "=", "master:purpose")], limit=1)
        self.assertEqual(branch_log.status, "error")
        self.assertEqual(purpose_log.status, "ok", "a failing feed must not abort the rest of the run")

    def test_sync_log_records_counts(self):
        self._register_all_feeds()

        self.sync.action_sync_now()

        log = self.env["custom.esb.sync.log"].search([("operation", "=", "master:branch")], limit=1)
        self.assertEqual(log.status, "ok")
        self.assertEqual(log.record_count, 2)
        self.assertEqual(log.created_count, 2)
