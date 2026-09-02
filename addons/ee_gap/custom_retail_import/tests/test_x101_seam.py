# -*- coding: utf-8 -*-
"""Tests for ``_x101_upsert_items`` — the shared product-upsert seam.

``_load_x101`` used to be one 320-line method. It was split so the MDM REST API could
reuse it instead of reimplementing the category tree, the Size/Inseam attributes, the
variant matcher and the multi-GTIN aliasing. The contract that split has to honour is
narrow and absolute:

**A product created through the API must be indistinguishable from one created by the
file import.** Not "equivalent" — the same external IDs, the same
``product.attribute.value`` records, the same ``default_code``, the same barcode
aliases. Anything less and X24DN sales rows resolve against one and not the other,
which is the exact failure the strict-product mode exists to prevent.

So the golden test here feeds the same data through both entry points and compares the
resulting database state field by field. The rest of the suite pins the properties the
extraction could plausibly have broken: optional ``log``/``row_to_line``, the commit
helper being inert under the test runner, idempotency of a re-run, and the attribute
backfill that lets a size appear in a later file than its template.
"""

from __future__ import annotations

import base64
import io

from odoo.tests.common import TransactionCase, tagged

#: (product_code, description, brand, category, klass, subclass, sku, size, inseam,
#:  gtin, retail_price, price_eff) -- the logical fields of the X101 column map.
_ROWS = [
    (
        "ZT01A-0001",
        "BLR MB 5PKT 555",
        "RETAIL",
        "MENS BOTTOMS",
        "JEANS",
        "SLIM",
        "ZT01A000103228",
        "32",
        "28",
        "5401231363516",
        749900,
        "2026-01-01 00:00:00",
    ),
    (
        "ZT01A-0001",
        "BLR MB 5PKT 555",
        "RETAIL",
        "MENS BOTTOMS",
        "JEANS",
        "SLIM",
        "ZT01A000103230",
        "32",
        "30",
        "5401231363523",
        749900,
        "2026-01-01 00:00:00",
    ),
    (
        "ZT01A-0001",
        "BLR MB 5PKT 555",
        "RETAIL",
        "MENS BOTTOMS",
        "JEANS",
        "SLIM",
        "ZT01A000103428",
        "34",
        "28",
        "5401231363530",
        749900,
        "2026-01-01 00:00:00",
    ),
    (
        "ZT01B-0002",
        "GRAPHIC TEE",
        "RETAIL",
        "MENS TOPS",
        "TEES",
        "SHORT SLEEVE",
        "ZT01B00020L",
        "L",
        "-",
        "5401231399999",
        399900,
        "2026-01-01 00:00:00",
    ),
]

#: 1-based sheet column for each tuple position, per profile_retail_x101's column map.
_COLUMNS = [2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 15, 16]


def _build_xlsx(rows):
    """A minimal X101-shaped workbook: two title rows, then data (data_start_row=3)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["X101 Material Master"])
    ws.append([])
    for row in rows:
        cells = [None] * max(_COLUMNS)
        for value, column in zip(row, _COLUMNS, strict=False):
            cells[column - 1] = value
        ws.append(cells)
    buffer = io.BytesIO()
    wb.save(buffer)
    return base64.b64encode(buffer.getvalue())


def _as_records(rows, first_row=3):
    """The same rows as the dicts ``read_records`` would produce."""
    keys = (
        "product_code",
        "description",
        "brand",
        "category",
        "klass",
        "subclass",
        "sku",
        "size",
        "inseam",
        "gtin",
        "retail_price",
        "price_eff",
    )
    return [dict(zip(keys, row, strict=False), _row=first_row + index) for index, row in enumerate(rows)]


@tagged("post_install", "-at_install")
class TestX101Seam(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Executor = cls.env["retail.import.executor"]
        cls.profile = cls.env.ref("custom_retail_import.profile_retail_x101")
        cls.namespace = cls.profile.namespace

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _snapshot(self, codes, skus):
        """The observable state both entry points must agree on."""
        Template = self.env["product.template"]
        Product = self.env["product.product"]
        Executor = self.Executor
        state = {"templates": {}, "variants": {}, "categories": {}}

        for code in codes:
            tmpl_id = Executor._xid_get(self.namespace, Executor._safe_xid("tmpl_", code), "product.template")
            if not tmpl_id:
                state["templates"][code] = None
                continue
            tmpl = Template.browse(tmpl_id)
            state["templates"][code] = {
                "name": tmpl.name,
                "default_code": tmpl.default_code,
                "list_price": tmpl.list_price,
                "type": tmpl.type,
                "is_storable": tmpl.is_storable,
                "categ": tmpl.categ_id.complete_name,
                "attributes": sorted(
                    (line.attribute_id.name, tuple(sorted(line.value_ids.mapped("name"))))
                    for line in tmpl.attribute_line_ids
                ),
                "variant_count": len(tmpl.product_variant_ids),
            }

        for sku in skus:
            product = Product.search([("default_code", "=", sku)], limit=1)
            state["variants"][sku] = (
                None
                if not product
                else {
                    "template": product.product_tmpl_id.default_code,
                    "barcode": product.barcode,
                    "aliases": sorted(product.barcode_ids.mapped("barcode")),
                    "values": sorted(product.product_template_variant_value_ids.mapped("name")),
                }
            )

        for row in _ROWS:
            for xid in (
                Executor._safe_xid("cat_l1_", row[3]),
                Executor._safe_xid("cat_l2_", f"{row[3]}_{row[4]}"),
                Executor._safe_xid("cat_l3_", f"{row[3]}_{row[4]}_{row[5]}"),
            ):
                categ_id = Executor._xid_get(self.namespace, xid, "product.category")
                state["categories"][xid] = (
                    self.env["product.category"].browse(categ_id).complete_name if categ_id else None
                )
        return state

    def _make_log(self):
        return self.env["retail.import.log"].create({"profile_id": self.profile.id, "filename": "test.xlsx"})

    # ------------------------------------------------------------------
    # The contract
    # ------------------------------------------------------------------
    def test_file_and_seam_produce_identical_state(self):
        """The golden test: XLSX import vs direct seam call, compared field by field.

        Both runs happen in this one transaction, so the second must be given rows
        that build *different* codes -- otherwise it would just re-find the first
        run's records and the comparison would be vacuous. The rows are therefore
        rewritten onto a parallel set of codes and the two snapshots are compared with
        those codes normalised away.
        """
        codes = sorted({row[0] for row in _ROWS})
        skus = [row[6] for row in _ROWS]

        log = self._make_log()
        self.Executor._load_x101(self.profile, _build_xlsx(_ROWS), log)
        from_file = self._snapshot(codes, skus)

        # A parallel set of codes: same shape, different identifiers.
        shifted = []
        for row in _ROWS:
            row = list(row)
            row[0] = row[0].replace("ZT01A", "ZT91A").replace("ZT01B", "ZT91B")
            row[6] = row[6].replace("ZT01A", "ZT91A").replace("ZT01B", "ZT91B")
            row[9] = "9" + row[9][1:]
            shifted.append(tuple(row))
        shifted_codes = sorted({row[0] for row in shifted})
        shifted_skus = [row[6] for row in shifted]

        summary = self.Executor._x101_upsert_items(_as_records(shifted), self.namespace)
        from_seam = self._snapshot(shifted_codes, shifted_skus)

        self.assertEqual(summary["created"], len(shifted_codes))
        self.assertEqual(summary["matched"], len(shifted_skus))
        self.assertEqual(summary["unmatched"], 0)

        def _normalise(blob):
            text = repr(blob)
            for old, new in (("ZT91A", "AAAAA"), ("ZT01A", "AAAAA"), ("ZT91B", "BBBBB"), ("ZT01B", "BBBBB")):
                text = text.replace(old, new)
            return text

        for code, shifted_code in zip(codes, shifted_codes, strict=False):
            self.assertEqual(
                _normalise(from_file["templates"][code]),
                _normalise(from_seam["templates"][shifted_code]),
                f"template {code} differs between the file and the seam",
            )
        for sku, shifted_sku in zip(skus, shifted_skus, strict=False):
            file_variant = from_file["variants"][sku]
            seam_variant = from_seam["variants"][shifted_sku]
            self.assertIsNotNone(file_variant, f"{sku} not created by the file import")
            self.assertIsNotNone(seam_variant, f"{shifted_sku} not created by the seam")
            self.assertEqual(file_variant["values"], seam_variant["values"])
            self.assertEqual(len(file_variant["aliases"]), len(seam_variant["aliases"]))
            self.assertEqual(_normalise(file_variant["template"]), _normalise(seam_variant["template"]))

        # Both runs share one category tree -- the seam must reuse it, not fork it.
        self.assertEqual(from_file["categories"], from_seam["categories"])

    # ------------------------------------------------------------------
    # Properties the extraction could have broken
    # ------------------------------------------------------------------
    def test_seam_without_log_writes_no_import_lines(self):
        """The API path passes log=None and must still write products."""
        before = self.env["retail.import.line"].search_count([])
        summary = self.Executor._x101_upsert_items(_as_records(_ROWS), self.namespace)
        self.assertEqual(self.env["retail.import.line"].search_count([]), before)
        self.assertTrue(summary["templates"])
        self.assertTrue(summary["variants"])

    def test_commit_helper_is_inert_under_tests(self):
        """_ri_commit must not escape the TransactionCase rollback."""
        marker = self.env["product.category"].create({"name": "seam-commit-probe"})
        self.Executor._ri_commit()
        self.assertTrue(marker.exists())
        self.assertFalse(self.env.cr._closed if hasattr(self.env.cr, "_closed") else False)

    def test_rerun_is_idempotent(self):
        first = self.Executor._x101_upsert_items(_as_records(_ROWS), self.namespace)
        second = self.Executor._x101_upsert_items(_as_records(_ROWS), self.namespace)
        self.assertEqual(second["created"], 0, "a re-run must not create templates again")
        self.assertEqual(second["templates"], first["templates"])
        self.assertEqual(second["variants"], first["variants"])
        for sku in (row[6] for row in _ROWS):
            product = self.env["product.product"].search([("default_code", "=", sku)])
            self.assertEqual(len(product), 1, f"{sku} duplicated on re-run")
            aliases = product.barcode_ids.mapped("barcode")
            self.assertEqual(len(aliases), len(set(aliases)), "barcode aliases duplicated")

    def test_later_file_backfills_a_new_size(self):
        """A size that appears only in a later batch still gets its variant."""
        self.Executor._x101_upsert_items(_as_records(_ROWS), self.namespace)
        extra = [
            (
                "ZT01A-0001",
                "BLR MB 5PKT 555",
                "RETAIL",
                "MENS BOTTOMS",
                "JEANS",
                "SLIM",
                "ZT01A000103628",
                "36",
                "28",
                "5401231363547",
                749900,
                "2026-02-01 00:00:00",
            )
        ]
        summary = self.Executor._x101_upsert_items(_as_records(extra), self.namespace)
        self.assertEqual(summary["created"], 0, "the template already existed")
        self.assertIn("ZT01A000103628", summary["variants"])
        product = self.env["product.product"].search([("default_code", "=", "ZT01A000103628")])
        self.assertEqual(len(product), 1)
        self.assertEqual(sorted(product.product_template_variant_value_ids.mapped("name")), ["28", "36"])

    def test_all_gtins_of_a_sku_are_kept(self):
        """One variant, several GTINs: every one must resolve, none may overwrite."""
        rows = list(_ROWS) + [
            (
                "ZT01A-0001",
                "BLR MB 5PKT 555",
                "RETAIL",
                "MENS BOTTOMS",
                "JEANS",
                "SLIM",
                "ZT01A000103228",
                "32",
                "28",
                "5401231399123",
                749900,
                "2026-01-01 00:00:00",
            )
        ]
        self.Executor._x101_upsert_items(_as_records(rows), self.namespace)
        product = self.env["product.product"].search([("default_code", "=", "ZT01A000103228")])
        resolvable = {product.barcode} | set(product.barcode_ids.mapped("barcode"))
        self.assertIn("5401231363516", resolvable)
        self.assertIn("5401231399123", resolvable)
        for code in ("5401231363516", "5401231399123"):
            self.assertEqual(self.env["product.product"]._resolve_barcode(code), product)

    def test_data_quality_is_reported_not_swallowed(self):
        bad = [
            (
                "ZT0QQ-0009",
                "NO CATEGORY NO PRICE",
                "RETAIL",
                "",
                "",
                "",
                "ZT0QQ00090M",
                "M",
                "-",
                "",
                0,
                "2026-01-01 00:00:00",
            )
        ]
        summary = self.Executor._x101_upsert_items(_as_records(bad), self.namespace)
        issues = summary["quality"].get("ZT0QQ-0009", [])
        self.assertIn("missing category", issues)
        self.assertIn("invalid/zero price", issues)
