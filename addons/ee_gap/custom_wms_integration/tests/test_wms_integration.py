# -*- coding: utf-8 -*-
"""WMS integration tests."""

from __future__ import annotations

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_integration")
class TestWmsIntegration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.Event = cls.env["wms.integration.event"]
        cls.Mapping = cls.env["wms.integration.mapping"]
        cls.Picking = cls.env["stock.picking"]

        cls.product = cls.env["product.product"].create(
            {
                "name": "WMS Widget",
                "type": "consu",
                "is_storable": True,
                "default_code": "WMS-SKU-1",
            }
        )
        cls.bin = cls.env["stock.location"].create(
            {
                "name": "BIN-WMS-1",
                "usage": "internal",
                "location_id": cls.stock_loc.id,
                "barcode": "BINBC-1",
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "WMS Vendor", "ref": "VEND-1"})

    # ------------------------------------------------------------------
    # A) inbound ASN idempotency
    # ------------------------------------------------------------------

    def _asn_payload(self, ref="ASN-0001", qty=5.0):
        return {
            "external_ref": ref,
            "partner_ref": "VEND-1",
            "warehouse_code": self.warehouse.code,
            "expected_date": "2026-07-30 08:00:00",
            "lines": [{"sku": "WMS-SKU-1", "qty": qty, "uom": self.product.uom_id.name}],
        }

    def test_asn_is_idempotent_on_external_ref(self):
        picking1, created1, warn1 = self.Picking._wms_upsert_from_host(self._asn_payload(), "incoming")
        self.assertTrue(created1)
        self.assertEqual(warn1, [])
        self.assertEqual(picking1.wms_external_ref, "ASN-0001")
        self.assertEqual(picking1.picking_type_id.code, "incoming")
        self.assertEqual(len(picking1.move_ids), 1)
        self.assertEqual(picking1.move_ids.product_uom_qty, 5.0)
        self.assertEqual(picking1.partner_id, self.partner)

        # Same reference again: update, never a second picking.
        picking2, created2, _warn2 = self.Picking._wms_upsert_from_host(self._asn_payload(qty=9.0), "incoming")
        self.assertFalse(created2)
        self.assertEqual(picking1, picking2)
        self.assertEqual(
            self.Picking.search_count([("wms_external_ref", "=", "ASN-0001")]),
            1,
            "a repeated ASN must not create a second picking",
        )
        self.assertEqual(picking2.move_ids.product_uom_qty, 9.0, "the redelivered ASN should replace the lines")

    def test_do_creates_outgoing_picking(self):
        payload = self._asn_payload(ref="DO-0001")
        picking, created, _warn = self.Picking._wms_upsert_from_host(payload, "outgoing")
        self.assertTrue(created)
        self.assertEqual(picking.picking_type_id.code, "outgoing")

    def test_asn_unknown_sku_is_a_warning_not_a_crash(self):
        payload = self._asn_payload(ref="ASN-0002")
        payload["lines"].append({"sku": "NOPE-999", "qty": 1})
        picking, _created, warnings = self.Picking._wms_upsert_from_host(payload, "incoming")
        self.assertEqual(len(picking.move_ids), 1, "only the resolvable line becomes a move")
        self.assertTrue(any("NOPE-999" in w for w in warnings))

    # ------------------------------------------------------------------
    # B) mapping resolution + fallback
    # ------------------------------------------------------------------

    def test_mapping_falls_back_to_default_code(self):
        resolved = self.Mapping._resolve("WMS-SKU-1", "product.product", self.env.company)
        self.assertEqual(resolved, self.product)

    def test_mapping_falls_back_to_location_barcode_then_name(self):
        self.assertEqual(self.Mapping._resolve("BINBC-1", "stock.location", self.env.company), self.bin)
        self.assertEqual(self.Mapping._resolve("BIN-WMS-1", "stock.location", self.env.company), self.bin)

    def test_explicit_mapping_wins_over_fallback(self):
        other = self.env["product.product"].create(
            {"name": "WMS Widget 2", "type": "consu", "is_storable": True, "default_code": "WMS-SKU-2"}
        )
        self.Mapping.create(
            {
                "external_code": "WMS-SKU-1",
                "internal_model": "product.product",
                "internal_res_id": other.id,
                "direction": "inbound",
            }
        )
        self.assertEqual(
            self.Mapping._resolve("WMS-SKU-1", "product.product", self.env.company),
            other,
            "an explicit mapping row must beat the default_code fallback",
        )
        # ...but only in its own direction.
        self.assertEqual(
            self.Mapping._resolve("WMS-SKU-1", "product.product", self.env.company, direction="outbound"),
            self.product,
        )

    def test_mapping_unknown_code_returns_empty(self):
        self.assertFalse(self.Mapping._resolve("DOES-NOT-EXIST", "product.product", self.env.company))
        self.assertFalse(self.Mapping._resolve("", "stock.location", self.env.company))

    def test_external_code_for_reverses(self):
        self.assertEqual(self.Mapping._external_code_for(self.product), "WMS-SKU-1")
        self.Mapping.create(
            {
                "external_code": "HOST-SKU-X",
                "internal_model": "product.product",
                "internal_res_id": self.product.id,
                "direction": "outbound",
            }
        )
        self.assertEqual(self.Mapping._external_code_for(self.product), "HOST-SKU-X")

    # ------------------------------------------------------------------
    # C) outbox enqueue on picking validate
    # ------------------------------------------------------------------

    def _validated_incoming_picking(self, ref="ASN-VAL"):
        picking, _created, _warn = self.Picking._wms_upsert_from_host(self._asn_payload(ref=ref), "incoming")
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        return picking

    def test_outbox_enqueued_on_picking_validate(self):
        picking = self._validated_incoming_picking()
        self.assertEqual(picking.state, "done")
        events = self.Event.search([("res_model", "=", "stock.picking"), ("res_id", "=", picking.id)])
        self.assertTrue(events, "validating an incoming picking must queue an outbound event")
        self.assertIn("goods_receipt", events.mapped("event_type"))
        event = events.filtered(lambda e: e.event_type == "goods_receipt")[:1]
        self.assertEqual(event.state, "pending")
        self.assertEqual(event.attempts, 0)
        self.assertTrue(event.name.startswith("WMSEVT/"))
        self.assertTrue(event.external_ref)
        self.assertEqual(event.payload.get("picking"), picking.name)
        self.assertEqual(event.payload["lines"][0]["sku"], "WMS-SKU-1")

    def test_event_is_append_only(self):
        event = self.Event.enqueue("goods_issue", payload={"a": 1})
        with self.assertRaises(Exception):
            event.write({"payload": {"a": 2}})
        # bookkeeping fields stay writable
        event.write({"state": "sent"})
        self.assertEqual(event.state, "sent")

    def test_ack_marks_the_row(self):
        event = self.Event.enqueue("goods_issue", payload={})
        event.write({"state": "sent"})
        acked = self.Event._ack(event.external_ref, host_ref="SAP-4711")
        self.assertEqual(acked, event)
        self.assertEqual(event.state, "acked")
        self.assertTrue(event.acked_at)
        self.assertFalse(self.Event._ack("UNKNOWN-REF"))

    def test_enqueue_rejects_unknown_event_type(self):
        with self.assertRaises(Exception):
            self.Event.enqueue("not_a_real_event", payload={})

    # ------------------------------------------------------------------
    # D) the hook must swallow errors, not roll back the picking
    # ------------------------------------------------------------------

    def test_hook_swallows_errors_without_rolling_back_the_picking(self):
        picking, _created, _warn = self.Picking._wms_upsert_from_host(self._asn_payload(ref="ASN-BOOM"), "incoming")
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True

        picking_cls = type(picking)

        def _explode(self, event_type):
            raise RuntimeError("host payload builder exploded")

        with patch.object(picking_cls, "_wms_picking_payload", _explode):
            picking.button_validate()

        self.assertEqual(picking.state, "done", "an integration failure must not undo the transfer")
        self.assertFalse(
            self.Event.search([("res_model", "=", "stock.picking"), ("res_id", "=", picking.id)]),
            "nothing should have been queued when the payload builder failed",
        )
        # The cursor must still be usable — a poisoned transaction would blow up here.
        self.assertTrue(self.Event.search_count([]) >= 0)
        self.env["res.partner"].create({"name": "post-failure write probe"})

    def test_safe_enqueue_never_raises(self):
        event_cls = type(self.Event)

        def _explode(self, *args, **kwargs):
            raise RuntimeError("insert failed")

        with patch.object(event_cls, "enqueue", _explode):
            result = self.Event._safe_enqueue("goods_receipt", payload={})
        self.assertFalse(result, "_safe_enqueue returns an empty recordset instead of raising")
        self.env["res.partner"].create({"name": "post-savepoint write probe"})

    # ------------------------------------------------------------------
    # E) drain / adapter wiring
    # ------------------------------------------------------------------

    def test_drain_without_adapter_config_is_a_noop(self):
        self.env["custom.adapter.config"].sudo().search([("adapter_type", "in", ("wms_host", "wms_sap_host"))]).write(
            {"status": "disabled"}
        )
        event = self.Event.enqueue("goods_receipt", payload={})
        self.assertEqual(self.Event._cron_drain_outbox(), 0)
        self.assertEqual(event.state, "pending", "an unconfigured host leaves events queued, never lost")
        self.assertIn("No active", event.last_error or "")

    def test_adapters_are_registered(self):
        from odoo.addons.custom_adapter_framework.models.adapter_registry import get_adapter_class

        self.assertIsNotNone(get_adapter_class("wms_host"))
        self.assertIsNotNone(get_adapter_class("wms_sap_host"))
        cls = get_adapter_class("wms_host")
        self.assertEqual(cls(None).endpoint_for("goods_issue"), "wms/goods-issue")
        sap = get_adapter_class("wms_sap_host")
        self.assertEqual(sap(None).endpoint_for("goods_issue"), "sap/wms/goods-issue")
