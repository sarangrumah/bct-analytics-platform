# -*- coding: utf-8 -*-
"""Warehouse documents & labels tests."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_docs")
class TestWmsDocs(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_loc = cls.env.ref("stock.stock_location_stock")
        cls.customer_loc = cls.env.ref("stock.stock_location_customers")

        # Two bins, deliberately created in reverse alphabetical order so a
        # naive "creation order" walk would fail the ordering assertion.
        cls.bin_b = cls.env["stock.location"].create(
            {"name": "BIN-B02", "usage": "internal", "location_id": cls.stock_loc.id}
        )
        cls.bin_a = cls.env["stock.location"].create(
            {"name": "BIN-A01", "usage": "internal", "location_id": cls.stock_loc.id}
        )

        cls.partner = cls.env["res.partner"].create({"name": "WMS Docs Customer"})

        def _product(name, code, weight, barcode=None, price=0.0):
            return cls.env["product.product"].create(
                {
                    "name": name,
                    "default_code": code,
                    "type": "consu",
                    "is_storable": True,
                    "weight": weight,
                    "barcode": barcode,
                    "list_price": price,
                }
            )

        # In bin A: codes Z then A → walk path must sort A before Z inside the bin.
        cls.p_a_z = _product("Docs Zulu", "DOC-Z", 1.0, "1000000000017", 5.0)
        cls.p_a_a = _product("Docs Alpha", "DOC-A", 2.5, "1000000000024", 7.0)
        # In bin B.
        cls.p_b = _product("Docs Bravo", "DOC-B", 0.5, "1000000000031", 9.0)

        cls.env["stock.quant"]._update_available_quantity(cls.p_a_z, cls.bin_a, 10.0)
        cls.env["stock.quant"]._update_available_quantity(cls.p_a_a, cls.bin_a, 10.0)
        cls.env["stock.quant"]._update_available_quantity(cls.p_b, cls.bin_b, 10.0)

        cls.package_type = cls.env["stock.package.type"].create(
            {
                "name": "DOCS BOX",
                "packaging_length": 40.0,
                "width": 30.0,
                "height": 20.0,
                "base_weight": 1.5,
                "max_weight": 25.0,
                "barcode": "PKGTYPE-DOCS",
            }
        )
        # Odoo 19: stock.quant.package was renamed to stock.package.
        cls.package = cls.env["stock.package"].create(
            {"name": "PACK-DOCS-0001", "package_type_id": cls.package_type.id}
        )

        cls.picking = cls._make_picking()

    @classmethod
    def _make_picking(cls):
        picking_type = cls.warehouse.out_type_id
        picking = cls.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "partner_id": cls.partner.id,
                "location_id": cls.stock_loc.id,
                "location_dest_id": cls.customer_loc.id,
                "origin": "SO-DOCS-001",
            }
        )
        for product, qty, src in (
            (cls.p_b, 4.0, cls.bin_b),
            (cls.p_a_z, 3.0, cls.bin_a),
            (cls.p_a_a, 2.0, cls.bin_a),
        ):
            # Odoo 19 removed stock.move.name; use reference/description_picking.
            cls.env["stock.move"].create(
                {
                    "reference": picking.name,
                    "description_picking": product.display_name,
                    "product_id": product.id,
                    "product_uom": product.uom_id.id,
                    "product_uom_qty": qty,
                    "picking_id": picking.id,
                    "location_id": src.id,
                    "location_dest_id": cls.customer_loc.id,
                    "company_id": picking.company_id.id,
                }
            )
        picking.action_confirm()
        picking.action_assign()
        return picking

    # ------------------------------------------------------------------
    # 1. Walk path
    # ------------------------------------------------------------------
    def test_pick_lines_walk_order(self):
        lines = self.picking._wms_pick_lines()
        self.assertTrue(lines, "action_assign should have produced move lines")
        keys = [(ml.location_id.complete_name, ml.product_id.default_code) for ml in lines]
        self.assertEqual(keys, sorted(keys), "walk path must be location then product code")
        # BIN-A01 comes before BIN-B02, and inside BIN-A01 DOC-A before DOC-Z.
        codes = [ml.product_id.default_code for ml in lines]
        self.assertEqual(codes.index("DOC-A"), 0)
        self.assertEqual(codes.index("DOC-Z"), 1)
        self.assertEqual(codes.index("DOC-B"), 2)

        rows = self.picking._wms_pick_rows()
        self.assertEqual([r["seq"] for r in rows], list(range(1, len(rows) + 1)))
        totals = self.picking._wms_pick_totals()
        self.assertEqual(totals["line_count"], len(rows))

    # ------------------------------------------------------------------
    # 2. Packing blocks
    # ------------------------------------------------------------------
    def test_packing_blocks_weight_and_loose(self):
        # Put only the DOC-B line into a package; the rest stays loose.
        packed = self.picking.move_line_ids.filtered(lambda ml: ml.product_id == self.p_b)
        self.assertTrue(packed)
        packed.result_package_id = self.package

        blocks = self.picking._wms_packing_blocks()
        self.assertEqual(len(blocks), 2, "one package block + one loose block")

        pkg_block, loose_block = blocks[0], blocks[1]
        self.assertEqual(pkg_block["package"], self.package)
        self.assertEqual(pkg_block["dims"], (40.0, 30.0, 20.0))
        # 4 units x 0.5 kg = 2.0 net, + 1.5 kg base weight = 3.5 gross.
        self.assertAlmostEqual(pkg_block["net_weight"], 2.0, places=3)
        self.assertAlmostEqual(pkg_block["gross_weight"], 3.5, places=3)

        self.assertFalse(loose_block["package"])
        self.assertEqual(loose_block["name"], "Loose / unpacked")
        # Loose lines carry no packaging tare, so gross == net.
        self.assertAlmostEqual(loose_block["gross_weight"], loose_block["net_weight"], places=6)
        # 3 x 1.0 (DOC-Z) + 2 x 2.5 (DOC-A) = 8.0
        self.assertAlmostEqual(loose_block["net_weight"], 8.0, places=3)

        totals = self.picking._wms_packing_totals()
        self.assertEqual(totals["package_count"], 1)
        self.assertAlmostEqual(totals["gross_weight"], 11.5, places=3)

    def test_barcode_rows(self):
        self.picking.move_line_ids.filtered(lambda ml: ml.product_id == self.p_b).result_package_id = self.package
        rows = self.picking._wms_barcode_rows()
        values = [r["value"] for r in rows]
        self.assertIn("PACK-DOCS-0001", values)
        self.assertIn(self.p_a_a.barcode, values)
        self.assertEqual(len(values), len(set(values)), "rows must be de-duplicated")
        for row in rows:
            self.assertTrue(row["qr_src"].startswith("/report/barcode/QR/"))
            self.assertTrue(row["code128_src"].startswith("/report/barcode/Code128/"))
        pairs = self.picking._wms_barcode_row_pairs(2)
        self.assertEqual(sum(len(p) for p in pairs), len(rows))

    # ------------------------------------------------------------------
    # 3. Label wizard
    # ------------------------------------------------------------------
    def test_label_wizard_manual_expansion(self):
        wizard = self.env["custom.wms.label.wizard"].create(
            {
                "product_ids": [(6, 0, (self.p_a_a + self.p_b).ids)],
                "qty_source": "manual",
                "qty_per_product": 3,
            }
        )
        labels = wizard._expand_labels()
        self.assertEqual(len(labels), 6)
        action = wizard.action_print()
        self.assertEqual(action.get("type"), "ir.actions.report")
        self.assertEqual(action["data"]["barcode_kind"], "QR")

    def test_label_wizard_picking_expansion(self):
        wizard = self.env["custom.wms.label.wizard"].create(
            {
                "picking_id": self.picking.id,
                "qty_source": "picking_qty",
            }
        )
        labels = wizard._expand_labels()
        # 4 + 3 + 2 reserved units.
        self.assertEqual(len(labels), 9)

    def test_label_cap_raises_user_error(self):
        self.env["ir.config_parameter"].sudo().set_param("custom_wms_docs.max_labels", "5")
        wizard = self.env["custom.wms.label.wizard"].create(
            {
                "product_ids": [(6, 0, (self.p_a_a + self.p_b).ids)],
                "qty_source": "manual",
                "qty_per_product": 10,
            }
        )
        with self.assertRaises(UserError) as ctx:
            wizard._expand_labels()
        self.assertIn("5", str(ctx.exception))
        self.assertIn("custom_wms_docs.max_labels", str(ctx.exception))

    # ------------------------------------------------------------------
    # 4. Templates render
    # ------------------------------------------------------------------
    def test_reports_render(self):
        self.picking.move_line_ids.filtered(lambda ml: ml.product_id == self.p_b).result_package_id = self.package
        Report = self.env["ir.actions.report"]
        for xmlid in (
            "custom_wms_docs.report_wms_picking_list",
            "custom_wms_docs.report_wms_packing_list",
            "custom_wms_docs.report_wms_barcode_list",
        ):
            html, kind = Report._render_qweb_html(xmlid, self.picking.ids)
            self.assertEqual(kind, "html")
            self.assertIn(b"<main>", html)
            self.assertIn(self.picking.name.encode(), html)

        labels = [{"product_id": self.p_a_a.id}, {"product_id": self.p_b.id}]
        html, kind = Report._render_qweb_html(
            "custom_wms_docs.report_wms_product_label",
            [self.p_a_a.id, self.p_b.id],
            data={"labels": labels, "label_kind": "price_tag", "barcode_kind": "QR"},
        )
        self.assertEqual(kind, "html")
        self.assertIn(b"<main>", html)
        self.assertIn(b"DOC-A", html)

    # ------------------------------------------------------------------
    # 5. Barcode rendering helpers
    # ------------------------------------------------------------------
    def test_barcode_png_and_data_uri(self):
        png = self.picking._wms_barcode_png("WH/OUT/00007")
        self.assertTrue(png.startswith(b"\x89PNG"), "must be a real PNG, not a URL")
        uri = self.picking._wms_barcode_src("WH/OUT/00007")
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        # An unrenderable payload degrades to nothing rather than exploding.
        self.assertEqual(self.picking._wms_barcode_png(""), b"")
        self.assertEqual(self.picking._wms_barcode_src(False), "")

    def test_item_barcode_prefers_lot_then_ean(self):
        line = self.picking.move_line_ids.filtered(lambda ml: ml.product_id == self.p_a_a)[:1]
        self.assertEqual(
            self.picking._wms_item_barcode_value(self.p_a_a, False),
            self.p_a_a.barcode,
            "an untracked line is keyed on the product EAN",
        )
        if line.lot_id:
            self.assertEqual(
                self.picking._wms_item_barcode_value(self.p_a_a, line.lot_id),
                line.lot_id.name,
                "a tracked line is keyed on the lot the handheld scans back",
            )

    def test_item_barcode_falls_back_to_code128(self):
        # "RPT/NOT-AN-EAN" cannot be an EAN-13, so 'auto' must degrade to
        # Code128 instead of returning an empty image.
        self.assertTrue(self.picking._wms_item_barcode_src("RPT/NOT-AN-EAN").startswith("data:image/png;base64,"))

    def test_picking_list_carries_line_item_barcodes(self):
        rows = self.picking._wms_pick_rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("item_barcode", row)
            self.assertIn("item_barcode_value", row)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "custom_wms_docs.report_wms_picking_list", self.picking.ids
        )[0]
        self.assertIn(b"Item Barcode", html)
        self.assertIn(b"data:image/png;base64,", html)
