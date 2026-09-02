# -*- coding: utf-8 -*-
"""WMS reporting pack tests: SQL views populate, spot-check sampling, PDF."""

from __future__ import annotations

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_reports")
class TestWmsReports(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.supplier_loc = cls.env.ref("stock.stock_location_suppliers")
        cls.bin = cls.env["stock.location"].create(
            {"name": "BIN-RPT", "usage": "internal", "location_id": cls.stock_loc.id}
        )
        cls.partner = cls.env["res.partner"].create({"name": "RPT Vendor"})
        cls.product = cls.env["product.product"].create(
            {
                "name": "RPT Widget",
                "type": "consu",
                "is_storable": True,
                "standard_price": 25.0,
                "default_code": "RPT-001",
            }
        )
        cls.env["stock.quant"]._update_available_quantity(cls.product, cls.bin, 40.0)

    def _done_move(self, src, dest, qty, partner=False):
        picking_type = self.env["stock.picking.type"].search(
            [("code", "=", "internal"), ("warehouse_id", "=", self.warehouse.id)], limit=1
        )
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": src.id,
                "location_dest_id": dest.id,
                "partner_id": partner and partner.id,
            }
        )
        move = self.env["stock.move"].create(
            {
                "product_id": self.product.id,
                "product_uom_qty": qty,
                "product_uom": self.product.uom_id.id,
                "location_id": src.id,
                "location_dest_id": dest.id,
                "picking_id": picking.id,
            }
        )
        picking.action_confirm()
        move.quantity = qty
        move.picked = True
        picking.button_validate()
        return move

    def test_stock_summary_view(self):
        rows = self.env["custom.wms.stock.summary.report"].search(
            [("product_id", "=", self.product.id), ("location_id", "=", self.bin.id)]
        )
        self.assertTrue(rows, "stock summary view should expose the seeded quant")
        row = rows[0]
        self.assertEqual(row.quantity, 40.0)
        self.assertEqual(row.warehouse_id, self.warehouse)
        self.assertAlmostEqual(row.value, 40.0 * 25.0)

    def test_purchase_return_view(self):
        move = self._done_move(self.bin, self.supplier_loc, 5.0, partner=self.partner)
        row = self.env["custom.wms.purchase.return.report"].search([("id", "=", move.id)])
        self.assertTrue(row, "done move to a supplier location should appear as a return")
        self.assertEqual(row.quantity, 5.0)
        self.assertEqual(row.partner_id, self.partner)
        self.assertGreater(row.value, 0.0)

    def test_transfer_report_view(self):
        move = self._done_move(self.bin, self.stock_loc, 3.0)
        row = self.env["custom.wms.transfer.report"].search([("id", "=", move.id)])
        self.assertTrue(row)
        self.assertEqual(row.transfer_kind, "internal")
        self.assertEqual(row.done_qty, 3.0)

    def test_spot_check_sampling_and_report(self):
        plan = self.env["custom.cycle.count.plan"].create(
            {
                "name": "Spot Plan",
                "warehouse_id": self.warehouse.id,
                "frequency": "adhoc",
                "method": "spot_check",
                "target_count_per_period": 50,
            }
        )
        wiz = self.env["custom.cycle.count.start.wizard"].create({"plan_id": plan.id})
        res = wiz.action_start()
        session = self.env["custom.cycle.count.session"].browse(res["res_id"])
        self.assertTrue(session.line_ids, "spot check should seed at least one line")
        sample_cap = int(
            self.env["ir.config_parameter"].sudo().get_param("custom_wms_reports.spot_check_sample_size", 10)
        )
        self.assertLessEqual(len(session.line_ids), sample_cap)

        rows = self.env["custom.wms.stock.take.report"].search([("session_id", "=", session.id)])
        self.assertEqual(len(rows), len(session.line_ids))
        self.assertTrue(all(r.method == "spot_check" for r in rows))

    def test_stock_take_pdf_renders(self):
        plan = self.env["custom.cycle.count.plan"].create(
            {
                "name": "PDF Plan",
                "warehouse_id": self.warehouse.id,
                "frequency": "adhoc",
                "method": "random",
            }
        )
        wiz = self.env["custom.cycle.count.start.wizard"].create({"plan_id": plan.id})
        res = wiz.action_start()
        session = self.env["custom.cycle.count.session"].browse(res["res_id"])
        html = self.env["ir.actions.report"]._render_qweb_html("custom_wms_reports.report_wms_stock_take", session.ids)[
            0
        ]
        self.assertIn(b"STOCK TAKE REPORT", html)
        self.assertIn(b"<main>", html)

    # ------------------------------------------------------------------
    # Scrap report + Scrap Note
    # ------------------------------------------------------------------
    def _validated_scrap(self, qty=3.0):
        # Top the bin up inside the test: the class-level quant is shared with
        # the move-based tests, so this helper must not depend on what is left.
        self.env["stock.quant"]._update_available_quantity(self.product, self.bin, qty)
        scrap = self.env["stock.scrap"].create(
            {
                "product_id": self.product.id,
                "product_uom_id": self.product.uom_id.id,
                "scrap_qty": qty,
                "location_id": self.bin.id,
                "origin": "RPT-SCRAP",
            }
        )
        scrap.action_validate()
        self.assertEqual(scrap.state, "done", "scrap did not validate — insufficient quantity?")
        return scrap

    def test_scrap_report_view(self):
        scrap = self._validated_scrap(3.0)
        rows = self.env["custom.wms.scrap.report"].search([("scrap_id", "=", scrap.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.state, "done")
        self.assertAlmostEqual(rows.scrap_qty, 3.0, places=3)
        # 3 x standard_price 25.0
        self.assertAlmostEqual(rows.scrap_value, 75.0, places=2)
        self.assertEqual(rows.location_id, self.bin)
        self.assertEqual(rows.warehouse_id, self.warehouse)

    def test_scrap_note_pdf_renders(self):
        scrap = self._validated_scrap(2.0)
        html = self.env["ir.actions.report"]._render_qweb_html("custom_wms_reports.report_wms_scrap_note", scrap.ids)[0]
        self.assertIn(b"SCRAP NOTE", html)
        self.assertIn(b"<main>", html)
        # Transaction-level barcode is embedded, not fetched over HTTP.
        self.assertIn(b"data:image/png;base64,", html)

    def test_scrap_note_groups_by_origin(self):
        first, second = self._validated_scrap(1.0), self._validated_scrap(2.0)
        both = first | second
        rows = both._wms_scrap_rows()
        self.assertEqual(len(rows), 2)
        totals = both._wms_scrap_totals()
        self.assertAlmostEqual(totals["total_qty"], 3.0, places=3)
        self.assertAlmostEqual(totals["total_value"], 75.0, places=2)
        # One shared origin -> that is the note reference.
        self.assertEqual(both._wms_scrap_header()["reference"], "RPT-SCRAP")

    # ------------------------------------------------------------------
    # XLSX export with embedded barcodes
    # ------------------------------------------------------------------
    def test_xlsx_export_embeds_barcodes(self):
        import base64
        import io
        import zipfile

        self._validated_scrap(4.0)
        rows = self.env["custom.wms.scrap.report"].search([("product_id", "=", self.product.id)])
        self.assertTrue(rows)

        action = rows.action_export_xlsx()
        self.assertEqual(action["type"], "ir.actions.act_url")
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[-1].split("?")[0]))
        data = base64.b64decode(attachment.datas)
        self.assertTrue(attachment.name.endswith(".xlsx"))

        book = zipfile.ZipFile(io.BytesIO(data))
        media = [n for n in book.namelist() if n.startswith("xl/media/")]
        # At least the document barcode and the line-item barcode.
        self.assertGreaterEqual(len(media), 2, "barcode images must be embedded in the workbook")
        sheet = book.read("xl/sharedStrings.xml")
        self.assertIn(b"Document Barcode", sheet)
        self.assertIn(b"Item Barcode", sheet)

    def test_xlsx_export_falls_back_to_the_whole_report(self):
        # An empty recordset means "no selection" in the list header, so the
        # engine exports everything the report holds rather than an empty file.
        self._validated_scrap(2.0)
        empty = self.env["custom.wms.scrap.report"].browse()
        action = empty.action_export_xlsx()
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[-1].split("?")[0]))
        self.assertTrue(attachment.datas)

    def test_every_report_declares_an_xlsx_shape(self):
        for model in (
            "custom.wms.transfer.report",
            "custom.wms.stock.summary.report",
            "custom.wms.stock.take.report",
            "custom.wms.purchase.return.report",
            "custom.wms.scrap.report",
        ):
            columns = self.env[model]._xlsx_columns()
            self.assertTrue(columns, "%s declares no XLSX columns" % model)
            for column in columns:
                self.assertIn("label", column)
                self.assertTrue(callable(column["value"]))
