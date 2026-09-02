# -*- coding: utf-8 -*-
"""GR extension tests: GS1 expiry/batch write-through + template import."""

from __future__ import annotations

import base64
import json

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_receiving_ext")
class TestReceivingExt(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.supplier_loc = cls.env.ref("stock.stock_location_suppliers")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.partner = cls.env["res.partner"].create({"name": "GR Vendor"})
        cls.lot_product = cls.env["product.product"].create(
            {
                "name": "GR Lot Widget",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "use_expiration_date": True,
                "expiration_time": 365,
                "barcode": "1234567890128",
            }
        )
        cls.serial_product = cls.env["product.product"].create(
            {
                "name": "GR Serial Device",
                "type": "consu",
                "is_storable": True,
                "tracking": "serial",
                "default_code": "GR-DEV-01",
            }
        )
        cls.receipt_type = cls.env["stock.picking.type"].search(
            [("code", "=", "incoming"), ("warehouse_id", "=", cls.warehouse.id)], limit=1
        )

    def _make_receipt(self, demands):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.receipt_type.id,
                "location_id": self.supplier_loc.id,
                "location_dest_id": self.stock_loc.id,
                "partner_id": self.partner.id,
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
        return picking

    def test_scan_apply_writes_expiry_and_batch(self):
        picking = self._make_receipt([(self.lot_product, 10)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self.env["custom.barcode.scan.line"].create(
            {
                "session_id": session.id,
                "product_id": self.lot_product.id,
                "raw_barcode": "0112345678901281 10BATCH-A1 17270115",
                "quantity": 5.0,
                "status": "ok",
                "supplier_batch_ref": "VND-77",
                "x_gs1_parsed": json.dumps({"gtin": "12345678901281", "lot": "BATCH-A1", "exp_date": "2027-01-15"}),
            }
        )
        session.action_apply_to_picking()
        lot = self.env["stock.lot"].search([("name", "=", "BATCH-A1"), ("product_id", "=", self.lot_product.id)])
        self.assertTrue(lot, "apply should have created the lot")
        self.assertEqual(
            fields.Date.to_date(lot.expiration_date),
            fields.Date.to_date("2027-01-15"),
            "GS1 AI 17 must land on stock.lot.expiration_date",
        )
        self.assertEqual(lot.supplier_batch_ref, "VND-77")

    def _scan_line(self, session, product, qty, lot):
        return self.env["custom.barcode.scan.line"].create(
            {
                "session_id": session.id,
                "product_id": product.id,
                "raw_barcode": lot,
                "quantity": qty,
                "status": "ok",
                "x_gs1_parsed": json.dumps({"lot": lot}),
            }
        )

    def test_scan_sets_quantity_instead_of_stacking_on_demand(self):
        picking = self._make_receipt([(self.lot_product, 18)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self._scan_line(session, self.lot_product, 18.0, "LOT-FULL")
        session.action_apply_to_picking()
        self.assertEqual(
            sum(picking.move_line_ids.mapped("quantity")),
            18.0,
            "the scanned count must SET the received qty, not add to the pre-filled demand",
        )

    def test_second_scan_session_adds_to_the_first(self):
        picking = self._make_receipt([(self.lot_product, 20)])
        first = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self._scan_line(first, self.lot_product, 12.0, "LOT-A")
        first.action_apply_to_picking()
        second = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self._scan_line(second, self.lot_product, 8.0, "LOT-B")
        second.action_apply_to_picking()
        self.assertEqual(
            sum(picking.move_line_ids.mapped("quantity")),
            20.0,
            "a second operator's scans must add to what the first already booked",
        )

    def test_reapplying_the_same_session_is_idempotent(self):
        picking = self._make_receipt([(self.lot_product, 15)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self._scan_line(session, self.lot_product, 15.0, "LOT-IDEM")
        session.action_apply_to_picking()
        session.action_apply_to_picking()
        self.assertEqual(sum(picking.move_line_ids.mapped("quantity")), 15.0)

    def test_gs1_serial_becomes_lot_name(self):
        picking = self._make_receipt([(self.serial_product, 2)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        self.env["custom.barcode.scan.line"].create(
            {
                "session_id": session.id,
                "product_id": self.serial_product.id,
                "raw_barcode": "0112345678901281 21356938035643809",
                "quantity": 1.0,
                "status": "ok",
                "x_gs1_parsed": json.dumps({"gtin": "12345678901281", "serial": "356938035643809"}),
            }
        )
        session.action_apply_to_picking()
        lot = self.env["stock.lot"].search(
            [("name", "=", "356938035643809"), ("product_id", "=", self.serial_product.id)]
        )
        self.assertTrue(lot, "GS1 AI 21 must become the serial lot name")
        ml = picking.move_line_ids.filtered(lambda l: l.lot_id == lot)
        self.assertEqual(len(ml), 1)
        self.assertEqual(ml.quantity, 1.0, "one serial = one unit")

    def test_bare_imei_scan_resolves_sole_serial_product(self):
        picking = self._make_receipt([(self.lot_product, 5), (self.serial_product, 1)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        session.on_barcode_scanned("356938035643817")
        line = session.line_ids[:1]
        self.assertEqual(line.status, "ok", "a bare IMEI must not be dropped as not_found")
        self.assertEqual(line.product_id, self.serial_product)
        self.assertEqual(line.lot_id.name, "356938035643817")

    def test_bare_imei_stays_not_found_when_ambiguous(self):
        other_serial = self.env["product.product"].create(
            {
                "name": "GR Serial Device 2",
                "type": "consu",
                "is_storable": True,
                "tracking": "serial",
                "default_code": "GR-DEV-02",
            }
        )
        picking = self._make_receipt([(self.serial_product, 1), (other_serial, 1)])
        session = self.env["custom.barcode.scan.session"].create({"picking_id": picking.id})
        session.on_barcode_scanned("356938035643825")
        self.assertEqual(
            session.line_ids[:1].status,
            "not_found",
            "two serial products on the picking is ambiguous — do not guess",
        )

    def test_import_csv_lot_and_serial(self):
        picking = self._make_receipt([(self.lot_product, 10), (self.serial_product, 2)])
        csv_content = (
            "barcode,serial,lot,qty,expiry,supplier_batch\r\n"
            "1234567890128,,LOT-CSV-1,4,15/01/2027,SUP-B9\r\n"
            "GR-DEV-01,SN-0001,,1,,\r\n"
            "GR-DEV-01,SN-0002,,1,,\r\n"
        )
        wiz = self.env["custom.wms.receipt.import.wizard"].create(
            {
                "picking_id": picking.id,
                "data_file": base64.b64encode(csv_content.encode()),
                "data_file_name": "template.csv",
            }
        )
        wiz.action_import()

        lot = self.env["stock.lot"].search([("name", "=", "LOT-CSV-1"), ("product_id", "=", self.lot_product.id)])
        self.assertTrue(lot)
        self.assertEqual(fields.Date.to_date(lot.expiration_date), fields.Date.to_date("2027-01-15"))
        self.assertEqual(lot.supplier_batch_ref, "SUP-B9")

        lot_ml = picking.move_line_ids.filtered(lambda l: l.lot_id == lot)
        self.assertEqual(len(lot_ml), 1)
        self.assertEqual(lot_ml.quantity, 4.0)

        serials = self.env["stock.lot"].search(
            [("product_id", "=", self.serial_product.id), ("name", "like", "SN-000%")]
        )
        self.assertEqual(len(serials), 2, "one serial lot per row")
        serial_mls = picking.move_line_ids.filtered(lambda l: l.product_id == self.serial_product and l.lot_id)
        self.assertEqual(len(serial_mls), 2)
        self.assertTrue(all(ml.quantity == 1.0 for ml in serial_mls))

    def test_import_rejects_bad_rows(self):
        picking = self._make_receipt([(self.lot_product, 10)])
        csv_content = (
            "barcode,serial,lot,qty,expiry,supplier_batch\r\nNO-SUCH-BARCODE,,L1,1,,\r\n1234567890128,,,3,,\r\n"
        )
        wiz = self.env["custom.wms.receipt.import.wizard"].create(
            {
                "picking_id": picking.id,
                "data_file": base64.b64encode(csv_content.encode()),
                "data_file_name": "bad.csv",
            }
        )
        with self.assertRaises(UserError) as err:
            wiz.action_import()
        message = str(err.exception)
        self.assertIn("NO-SUCH-BARCODE", message)
        self.assertIn("serial/lot is required", message)
        self.assertFalse(
            self.env["stock.lot"].search([("name", "=", "L1")]),
            "all-or-nothing: no lot may be created when any row fails",
        )
