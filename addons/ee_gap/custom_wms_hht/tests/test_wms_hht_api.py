# -*- coding: utf-8 -*-
"""WMS handheld API tests.

The controller methods are called directly (with a faked `request`) rather
than over HTTP: what needs proving is that each endpoint drives the real WMS
models correctly, not that Odoo can route a URL.
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import odoo.http

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.custom_wms_hht.controllers import wms_api


class _FakeRequest:
    """Only what the controller touches: env + params."""

    def __init__(self, env, params=None):
        self.env = env
        self.params = params or {}


@tagged("post_install", "-at_install", "custom_wms_hht")
class TestWmsHhtApi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.supplier_loc = cls.env.ref("stock.stock_location_suppliers")
        cls.customer_loc = cls.env.ref("stock.stock_location_customers")
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.partner = cls.env["res.partner"].create({"name": "HHT Vendor"})
        cls.bin_a = cls.env["stock.location"].create(
            {
                "name": "HHT-A-01",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
                "barcode": "HHT-A-01",
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "HHT Widget",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "barcode": "5901234123457",
                "default_code": "HHT-W1",
            }
        )
        cls.plain = cls.env["product.product"].create(
            {
                "name": "HHT Plain",
                "type": "consu",
                "is_storable": True,
                "barcode": "4006381333931",
                "default_code": "HHT-P1",
            }
        )
        cls.controller = wms_api.WmsHhtApi()

    # -- helpers -------------------------------------------------------

    @contextlib.contextmanager
    def _patch_request(self, params=None):
        """Fake the request in the controller AND in odoo.http.

        `_()` resolves the language through `odoo.http.request` first; with
        that left as None it falls through to frame inspection and dies on
        `NoneType.uid` inside a controller frame, turning every business
        error into an INTERNAL one.
        """
        fake = _FakeRequest(self.env, params)
        with patch.object(wms_api, "request", fake), patch.object(odoo.http, "request", fake):
            yield fake

    def _receipt(self, demands):
        ptype = self.env["stock.picking.type"].search(
            [("code", "=", "incoming"), ("warehouse_id", "=", self.warehouse.id)], limit=1
        )
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": ptype.id,
                "partner_id": self.partner.id,
                "location_id": self.supplier_loc.id,
                "location_dest_id": self.stock_loc.id,
            }
        )
        for product, qty in demands:
            self.env["stock.move"].create(
                {
                    "product_id": product.id,
                    "product_uom_qty": qty,
                    "product_uom": product.uom_id.id,
                    "location_id": self.supplier_loc.id,
                    "location_dest_id": self.stock_loc.id,
                    "picking_id": picking.id,
                }
            )
        picking.action_confirm()
        picking.action_assign()
        return picking

    def _delivery(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(product, self.bin_a, qty)
        ptype = self.env["stock.picking.type"].search(
            [("code", "=", "outgoing"), ("warehouse_id", "=", self.warehouse.id)], limit=1
        )
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": ptype.id,
                "partner_id": self.partner.id,
                "location_id": self.stock_loc.id,
                "location_dest_id": self.customer_loc.id,
            }
        )
        self.env["stock.move"].create(
            {
                "product_id": product.id,
                "product_uom_qty": qty,
                "product_uom": product.uom_id.id,
                "location_id": self.stock_loc.id,
                "location_dest_id": self.customer_loc.id,
                "picking_id": picking.id,
            }
        )
        picking.action_confirm()
        picking.action_assign()
        return picking

    # -- queue / scan --------------------------------------------------

    def test_queue_counts_open_work(self):
        self._receipt([(self.plain, 5)])
        with self._patch_request():
            res = self.controller.queue()
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["queue"]["receive"], 1)
        self.assertIn("pick", res["queue"])

    def test_scan_resolve_distinguishes_bin_from_product(self):
        with self._patch_request():
            loc = self.controller.scan_resolve(barcode="HHT-A-01")
            prod = self.controller.scan_resolve(barcode="4006381333931")
            miss = self.controller.scan_resolve(barcode="NOPE-999")
        self.assertEqual(loc["kind"], "location")
        self.assertEqual(prod["kind"], "product")
        self.assertEqual(prod["record"]["default_code"], "HHT-P1")
        self.assertFalse(miss["ok"])
        self.assertEqual(miss["error_code"], "NOT_FOUND")

    def test_scan_resolve_expect_rejects_wrong_kind(self):
        """A product scanned into a 'scan the bin' prompt must not resolve."""
        with self._patch_request():
            res = self.controller.scan_resolve(barcode="4006381333931", expect="location")
        self.assertFalse(res["ok"])

    def test_scan_resolve_reads_gs1(self):
        with self._patch_request():
            res = self.controller.scan_resolve(barcode="0105901234123457" + "10LOT-9")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["kind"], "product")
        self.assertEqual(res["gs1"].get("lot"), "LOT-9")

    # -- scan-to-open on the list screens ------------------------------

    def test_pickings_query_finds_a_receipt_by_its_own_number(self):
        picking = self._receipt([(self.plain, 3)])
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query=picking.name)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual([p["id"] for p in res["pickings"]], [picking.id])
        self.assertEqual(res["matched_by"], "name")

    def test_pickings_query_finds_a_receipt_by_the_vendor_document(self):
        """The operator scans the delivery note (origin), not WH/IN/xxxxx."""
        picking = self._receipt([(self.plain, 3)])
        picking.origin = "PO-VENDOR-4471"
        other = self._receipt([(self.plain, 1)])
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query="VENDOR-4471")
        self.assertTrue(res["ok"], res.get("error"))
        ids = [p["id"] for p in res["pickings"]]
        self.assertIn(picking.id, ids)
        self.assertNotIn(other.id, ids)
        self.assertEqual(res["matched_by"], "reference")

    def test_pickings_query_finds_receipts_by_a_scanned_item(self):
        """No readable paperwork: scanning a carton must still find the receipt."""
        picking = self._receipt([(self.plain, 3)])
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query="4006381333931")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertIn(picking.id, [p["id"] for p in res["pickings"]])
        self.assertEqual(res["matched_by"], "product")

    def test_pickings_query_prefers_an_exact_number_over_a_partial(self):
        picking = self._receipt([(self.plain, 3)])
        decoy = self._receipt([(self.plain, 1)])
        decoy.origin = f"re-do of {picking.name}"
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query=picking.name)
        self.assertEqual([p["id"] for p in res["pickings"]], [picking.id])

    def test_pickings_query_reports_a_validated_transfer_instead_of_nothing(self):
        picking = self._receipt([(self.plain, 2)])
        picking.move_line_ids.quantity = 2
        picking.button_validate()
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query=picking.name)
        self.assertTrue(res["ok"])
        self.assertEqual(res["matched_by"], "closed")
        self.assertEqual([p["state"] for p in res["pickings"]], ["done"])

    def test_pickings_query_that_matches_nothing_returns_an_empty_list(self):
        self._receipt([(self.plain, 2)])
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id, query="GHOST-999")
        self.assertTrue(res["ok"])
        self.assertEqual(res["pickings"], [])

    def test_pickings_without_a_query_is_the_full_open_list(self):
        picking = self._receipt([(self.plain, 2)])
        with self._patch_request():
            res = self.controller.pickings(code="incoming", warehouse_id=self.warehouse.id)
        self.assertIn(picking.id, [p["id"] for p in res["pickings"]])
        self.assertEqual(res["query"], "")

    def test_scan_resolve_picking_accepts_the_vendor_document(self):
        picking = self._receipt([(self.plain, 2)])
        picking.origin = "DN-88213"
        with self._patch_request():
            res = self.controller.scan_resolve(barcode="DN-88213", expect="picking")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["record"]["id"], picking.id)

    # -- receiving -----------------------------------------------------

    def test_receive_scan_sets_quantity_and_validates(self):
        picking = self._receipt([(self.plain, 6)])
        with self._patch_request():
            res = self.controller.receive_scan(picking_id=picking.id, barcode="4006381333931", quantity=6)
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(sum(picking.move_line_ids.mapped("quantity")), 6.0)
            done = self.controller.receive_validate(picking_id=picking.id)
        self.assertTrue(done["ok"], done.get("error"))
        self.assertEqual(picking.state, "done")

    def test_receive_scan_reports_unknown_barcode_without_leaving_a_line(self):
        picking = self._receipt([(self.plain, 1)])
        with self._patch_request():
            res = self.controller.receive_scan(picking_id=picking.id, barcode="GHOST-1")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "NOT_FOUND")
        session = self.env["custom.barcode.scan.session"].search([("picking_id", "=", picking.id)])
        self.assertFalse(session.line_ids, "a rejected scan must not leave a dangling line")

    # -- putaway -------------------------------------------------------

    def test_putaway_apply_moves_destination_to_scanned_bin(self):
        picking = self._receipt([(self.plain, 4)])
        ml = picking.move_line_ids[:1]
        with self._patch_request():
            res = self.controller.putaway_apply(move_line_id=ml.id, barcode="HHT-A-01")
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(ml.location_dest_id, self.bin_a)

    def test_putaway_apply_rejects_unknown_bin(self):
        picking = self._receipt([(self.plain, 4)])
        ml = picking.move_line_ids[:1]
        with self._patch_request():
            res = self.controller.putaway_apply(move_line_id=ml.id, barcode="NO-SUCH-BIN")
        self.assertFalse(res["ok"])

    def test_putaway_suggest_returns_ranked_bins(self):
        picking = self._receipt([(self.plain, 4)])
        with self._patch_request():
            res = self.controller.putaway_suggest(picking_id=picking.id)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(res["rows"]), len(picking.move_line_ids))
        self.assertIn("proposals", res["rows"][0])

    # -- picking -------------------------------------------------------

    def test_pick_confirm_rejects_the_wrong_item(self):
        picking = self._delivery(self.plain, 3)
        ml = picking.move_line_ids[:1]
        with self._patch_request():
            res = self.controller.pick_confirm(move_line_id=ml.id, barcode="5901234123457")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "MISMATCH")
        self.assertEqual(ml.quantity, 3.0, "a rejected scan must not change the line")

    def test_pick_confirm_then_validate(self):
        picking = self._delivery(self.plain, 3)
        ml = picking.move_line_ids[:1]
        with self._patch_request():
            ok = self.controller.pick_confirm(move_line_id=ml.id, barcode="4006381333931", quantity=3)
            self.assertTrue(ok["ok"], ok.get("error"))
            res = self.controller.pick_validate(picking_id=picking.id)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(picking.state, "done")

    def test_pick_pack_creates_a_package_with_contents(self):
        picking = self._delivery(self.plain, 3)
        ml = picking.move_line_ids[:1]
        with self._patch_request():
            self.controller.pick_confirm(move_line_id=ml.id, quantity=3)
            res = self.controller.pick_pack(picking_id=picking.id)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["package"], "put in pack must return the package")
        self.assertTrue(ml.result_package_id)

    # -- package -------------------------------------------------------

    def test_package_lookup_by_barcode(self):
        pack = self.env["stock.package"].create({"name": "HHT-PACK-1"})
        with self._patch_request():
            res = self.controller.package(barcode="HHT-PACK-1")
            missing = self.controller.package(barcode="HHT-PACK-NOPE")
        self.assertTrue(res["ok"])
        self.assertEqual(res["package"]["name"], pack.name)
        self.assertFalse(missing["ok"])

    # -- transfer orders / counting ------------------------------------
    #
    # These call the *list* endpoints against populated data on purpose: the
    # first version of bin2bin_list read `to.picking_id`, a field that does
    # not exist on custom.transfer.order, and no test noticed because the
    # fixture had no transfer order to serialise.

    def _transfer_order(self):
        bin_b = self.env["stock.location"].create(
            {
                "name": "HHT-B-01",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "barcode": "HHT-B-01",
            }
        )
        self.env["stock.quant"]._update_available_quantity(self.plain, self.bin_a, 10)
        move = self.env["custom.to.engine"].materialize(
            {
                "source_location_id": self.bin_a.id,
                "target_location_id": bin_b.id,
                "product_id": self.plain.id,
                "planned_qty": 4.0,
                "company_id": self.env.company.id,
            }
        )
        return self.env["custom.transfer.order"].search([("stock_move_id", "=", move.id)], limit=1), bin_b

    def test_bin2bin_list_serialises_a_real_order(self):
        order, _bin_b = self._transfer_order()
        with self._patch_request():
            res = self.controller.bin2bin_list()
        self.assertTrue(res["ok"], res.get("error"))
        names = [o["name"] for o in res["orders"]]
        self.assertIn(order.name, names)

    def test_bin2bin_list_query_finds_the_move_by_the_bin_you_stand_at(self):
        """Scanning a bin on the order list answers "anything to move here?"."""
        order, _bin_b = self._transfer_order()
        with self._patch_request():
            hit = self.controller.bin2bin_list(query="HHT-A-01")
            miss = self.controller.bin2bin_list(query="HHT-NOWHERE")
        self.assertIn(order.name, [o["name"] for o in hit["orders"]])
        self.assertEqual(miss["orders"], [])

    def test_bin2bin_execute_moves_the_stock(self):
        order, bin_b = self._transfer_order()
        with self._patch_request():
            res = self.controller.bin2bin_execute(
                transfer_order_id=order.id,
                source_barcode="HHT-A-01",
                target_barcode="HHT-B-01",
            )
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(order.stock_move_id.state, "done")
        moved = self.env["stock.quant"]._get_available_quantity(self.plain, bin_b)
        self.assertEqual(moved, 4.0)

    def test_bin2bin_execute_rejects_the_wrong_bin(self):
        order, _bin_b = self._transfer_order()
        with self._patch_request():
            res = self.controller.bin2bin_execute(transfer_order_id=order.id, source_barcode="HHT-B-01")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "MISMATCH")
        self.assertNotEqual(order.stock_move_id.state, "done")

    def _count_session(self):
        self.env["stock.quant"]._update_available_quantity(self.plain, self.bin_a, 7)
        plan = self.env["custom.cycle.count.plan"].create(
            {
                "name": "HHT Count Plan",
                "warehouse_id": self.warehouse.id,
                "method": "random",
                "company_id": self.env.company.id,
            }
        )
        wiz = self.env["custom.cycle.count.start.wizard"].create({"plan_id": plan.id, "target_count": 5})
        action = wiz.action_start()
        return self.env["custom.cycle.count.session"].browse(action["res_id"])

    def test_count_lines_and_submit(self):
        session = self._count_session()
        with self._patch_request():
            listed = self.controller.count_sessions()
            lines = self.controller.count_lines(session_id=session.id)
            self.assertTrue(lines["ok"], lines.get("error"))
            self.assertTrue(lines["lines"], "the session should expose its lines")
            line = lines["lines"][0]
            res = self.controller.count_submit(line_id=line["id"], quantity=line["expected_qty"] - 1)
        self.assertTrue(listed["ok"])
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["line"]["variance_qty"], -1.0)

    def test_count_sessions_query_finds_the_sheet_by_a_scanned_bin(self):
        session = self._count_session()
        with self._patch_request():
            hit = self.controller.count_sessions(query="HHT-A-01")
            miss = self.controller.count_sessions(query="HHT-NOWHERE")
        self.assertIn(session.id, [s["id"] for s in hit["sessions"]])
        self.assertEqual(miss["sessions"], [])

    # -- stock check ---------------------------------------------------

    def test_stock_lookup_reports_totals_and_bins(self):
        """On-hand is split per bin, and reservation is subtracted from free."""
        bin_b = self.env["stock.location"].create(
            {
                "name": "HHT-B-01",
                "usage": "internal",
                "location_id": self.stock_loc.id,
                "barcode": "HHT-B-01",
            }
        )
        self.env["stock.quant"]._update_available_quantity(self.plain, self.bin_a, 7)
        self.env["stock.quant"]._update_available_quantity(self.plain, bin_b, 3)
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="4006381333931", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        self.assertEqual(res["product"]["default_code"], "HHT-P1")
        self.assertEqual(res["totals"]["on_hand"], 10)
        self.assertEqual(res["totals"]["available"], 10)
        self.assertEqual(res["totals"]["bin_count"], 2)
        # Biggest bin first — the operator's first stop.
        self.assertEqual([b["barcode"] for b in res["bins"]], ["HHT-A-01", "HHT-B-01"])
        self.assertEqual([b["quantity"] for b in res["bins"]], [7, 3])

    def test_stock_lookup_subtracts_reserved_stock(self):
        self._delivery(self.plain, 4)  # puts 4 in bin_a and reserves them
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="HHT-P1", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        self.assertEqual(res["totals"]["on_hand"], 4)
        self.assertEqual(res["totals"]["reserved"], 4)
        self.assertEqual(res["totals"]["available"], 0)
        self.assertEqual(res["bins"][0]["available"], 0)

    def test_stock_lookup_groups_lots_under_one_bin(self):
        """Two lots in one bin are one walking stop, not two rows."""
        lot_a, lot_b = [
            self.env["stock.lot"].create({"name": name, "product_id": self.product.id})
            for name in ("HHT-LOT-A", "HHT-LOT-B")
        ]
        self.env["stock.quant"]._update_available_quantity(self.product, self.bin_a, 5, lot_id=lot_a)
        self.env["stock.quant"]._update_available_quantity(self.product, self.bin_a, 2, lot_id=lot_b)
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="5901234123457", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["bins"]), 1)
        row = res["bins"][0]
        self.assertEqual(row["quantity"], 7)
        self.assertEqual([l["name"] for l in row["lots"]], ["HHT-LOT-A", "HHT-LOT-B"])

    def test_stock_lookup_accepts_gs1_and_lot_labels(self):
        """The aisle label may be a GS1 string or a bare lot — both identify
        the product just as well as its EAN."""
        lot = self.env["stock.lot"].create({"name": "HHT-LOT-GS1", "product_id": self.product.id})
        self.env["stock.quant"]._update_available_quantity(self.product, self.bin_a, 1, lot_id=lot)
        with self._patch_request():
            by_gs1 = self.controller.stock_lookup(barcode="0105901234123457", warehouse_id=self.warehouse.id)
            by_lot = self.controller.stock_lookup(barcode="HHT-LOT-GS1", warehouse_id=self.warehouse.id)
        self.assertTrue(by_gs1["ok"])
        self.assertEqual(by_gs1["product"]["default_code"], "HHT-W1")
        self.assertTrue(by_lot["ok"])
        self.assertEqual(by_lot["product"]["default_code"], "HHT-W1")

    def test_stock_lookup_survives_no_access_to_putaway_config(self):
        """A stock check is for any operator; losing suggestions must not lose stock."""
        self.env["stock.quant"]._update_available_quantity(self.plain, self.bin_a, 5)
        with (
            self._patch_request(),
            patch.object(
                type(self.env["custom.putaway.engine"]),
                "propose_for_product",
                side_effect=AccessError("no putaway group"),
            ),
        ):
            res = self.controller.stock_lookup(barcode="4006381333931", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["suggestions_denied"])
        self.assertEqual(res["suggestions"], [])
        self.assertEqual(res["totals"]["on_hand"], 5.0)
        self.assertTrue(res["bins"], "the bins the operator came for must still be there")

    def test_stock_lookup_is_read_only(self):
        """A stock check must never move or reserve anything."""
        self.env["stock.quant"]._update_available_quantity(self.plain, self.bin_a, 6)
        before = self.env["stock.move.line"].search_count([])
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="HHT-P1", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        self.assertEqual(self.env["stock.move.line"].search_count([]), before)
        quant = self.env["stock.quant"].search(
            [("product_id", "=", self.plain.id), ("location_id", "=", self.bin_a.id)]
        )
        self.assertEqual(quant.quantity, 6)
        self.assertEqual(quant.reserved_quantity, 0)

    def test_stock_lookup_flags_a_zone_holding_stock_in_a_child_bin(self):
        """A rule usually targets a zone while the stock sits in a bin under
        it — comparing ids alone reported every such zone as empty."""
        zone = self.env["stock.location"].create(
            {"name": "HHT-ZONE", "usage": "view", "location_id": self.stock_loc.id}
        )
        child = self.env["stock.location"].create(
            {
                "name": "HHT-Z-01",
                "usage": "internal",
                "location_id": zone.id,
                "barcode": "HHT-Z-01",
            }
        )
        strategy = self.env["custom.wms.putaway.strategy"].create(
            {"name": "HHT Stock Check", "warehouse_id": self.warehouse.id, "rule_set": "custom"}
        )
        self.env["custom.wms.putaway.rule"].create(
            {
                "strategy_id": strategy.id,
                "name": "to the zone",
                "tier": 1,
                "kind": "fixed_location",
                "target_location_id": zone.id,
            }
        )
        self.env["stock.quant"]._update_available_quantity(self.plain, child, 9)
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="HHT-P1", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        zone_rows = [s for s in res["suggestions"] if s["location_id"] == zone.id]
        self.assertTrue(zone_rows, "the zone rule should have been proposed")
        self.assertTrue(zone_rows[0]["has_stock"])

    def test_stock_lookup_shows_each_suggested_bin_once(self):
        """Two rules ranking the same bin is one walk, not two rows."""
        strategy = self.env["custom.wms.putaway.strategy"].create(
            {"name": "HHT Dup", "warehouse_id": self.warehouse.id, "rule_set": "custom"}
        )
        for tier in (1, 2):
            self.env["custom.wms.putaway.rule"].create(
                {
                    "strategy_id": strategy.id,
                    "name": "rule %s" % tier,
                    "tier": tier,
                    "kind": "fixed_location",
                    "target_location_id": self.bin_a.id,
                }
            )
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="HHT-P1", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        ids = [s["location_id"] for s in res["suggestions"]]
        self.assertEqual(len(ids), len(set(ids)), "a bin must not be suggested twice")

    def test_stock_lookup_reports_unknown_barcode(self):
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="0000000000000", warehouse_id=self.warehouse.id)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "NOT_FOUND")

    def test_stock_lookup_zero_stock_is_not_an_error(self):
        """A product with nothing on hand is a valid answer, not a failure —
        "none here" is exactly what the operator walked over to find out."""
        with self._patch_request():
            res = self.controller.stock_lookup(barcode="HHT-P1", warehouse_id=self.warehouse.id)
        self.assertTrue(res["ok"])
        self.assertEqual(res["totals"]["on_hand"], 0)
        self.assertEqual(res["bins"], [])

    def test_every_list_endpoint_answers_ok(self):
        """Smoke: each screen's first call must not blow up on real data."""
        self._transfer_order()
        self._count_session()
        self._receipt([(self.plain, 2)])
        self._delivery(self.plain, 1)
        with self._patch_request():
            results = {
                "queue": self.controller.queue(),
                "warehouses": self.controller.warehouses(),
                "incoming": self.controller.pickings(code="incoming"),
                "internal": self.controller.pickings(code="internal"),
                "outgoing": self.controller.pickings(code="outgoing"),
                "bin2bin": self.controller.bin2bin_list(),
                "count": self.controller.count_sessions(),
            }
        broken = {k: v.get("error") for k, v in results.items() if not v["ok"]}
        self.assertFalse(broken, f"list endpoints failing: {broken}")

    # -- errors --------------------------------------------------------

    def test_business_error_is_json_not_a_traceback(self):
        """A UserError from the model must reach the handheld as {ok: false}."""
        picking = self._receipt([(self.plain, 5)])
        with self._patch_request():
            # Nothing scanned yet: validating an untouched receipt raises.
            res = self.controller.qc(picking_id=picking.id, verdict="pass")
        self.assertFalse(res["ok"])
        self.assertIn(res["error_code"], ("BUSINESS", "ACCESS", "INTERNAL"))
