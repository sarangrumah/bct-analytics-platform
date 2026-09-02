# -*- coding: utf-8 -*-
"""Ingest: staging, keying and the product records one message produces.

The contract being asserted is that a message is either fully staged with a key we can
dedupe and replay on, or refused outright — never half-staged. A message we cannot key
is a message we could never answer questions about later.
"""

from __future__ import annotations

from .common import MdmCase, item


class TestMdmIngest(MdmCase):
    def test_single_item_is_staged_and_applied(self):
        request, duplicate = self.ingest([item()])

        self.assertFalse(duplicate)
        self.assertEqual(request.item_count, 1)
        self.assertEqual(request.state, "partial" if request.review_count else "done")

        staged = request.item_ids
        self.assertEqual(staged.sku_code, "002IJ-00273228")
        self.assertEqual(staged.prod_sku, "002IJ002703228")
        self.assertEqual(staged.template_code, "002IJ-0027")
        self.assertEqual(staged.ean, "5401231363516")
        self.assertTrue(staged.content_hash)

    def test_default_code_comes_from_udf2_not_skucode(self):
        """The single most consequential mapping decision in this integration.

        X101's PROD SKU is ``PRODUCT_CODE without dashes + "0" + SIZE + INSEAM``, which
        is exactly ``udf2``. ``skuCode`` keeps the dash and exists nowhere in X101, so
        using it as the internal reference would fork the SKU namespace and every
        X24DN composite lookup would miss.
        """
        self.ingest([item()])

        variant = self.variant("002IJ002703228")
        self.assertTrue(variant, "the variant must be keyed on udf2")
        self.assertEqual(variant.mdm_sku_code, "002IJ-00273228", "skuCode is a secondary key")
        self.assertFalse(self.variant("002IJ-00273228"), "skuCode must not become a default_code")

    def test_template_variant_and_barcode(self):
        request, _dup = self.ingest([item()])
        staged = request.item_ids

        template = self.template("002IJ-0027")
        self.assertTrue(template)
        self.assertEqual(template.name, "BLR MB 5PKT 555 ZLATAN")
        self.assertEqual(staged.template_id, template)

        variant = self.variant("002IJ002703228")
        self.assertEqual(staged.product_id, variant)
        self.assertEqual(
            self.template_sizes("002IJ-0027"),
            [("Inseam", ("28",)), ("Size", ("32",))],
            "size 32 28 must land on the same Size/Inseam attributes the file import uses",
        )
        self.assertEqual(self.Product._resolve_barcode("5401231363516"), variant)

    def test_extended_attributes_are_recorded(self):
        self.ingest([item()])
        template = self.template("002IJ-0027")
        self.assertEqual(template.mdm_brand, "RETAIL")
        self.assertEqual(template.mdm_season, "Fall")
        self.assertEqual(template.mdm_gender, "MEN")
        self.assertEqual(template.mdm_segment, "SEASONAL FASHION")
        self.assertEqual(template.mdm_classification, "Normal")
        self.assertEqual(template.mdm_vendor_code, "LS")
        self.assertEqual(template.mdm_source, "mdm_api")
        self.assertTrue(template.mdm_content_hash)

    def test_unmapped_fields_are_kept_verbatim(self):
        """budf3/udf3/udf5/udf6/udf7 have no known meaning yet — do not invent one."""
        self.ingest([item()])
        raw = self.template("002IJ-0027").mdm_raw_json or {}
        for key in ("budf3", "udf3", "udf5", "udf6", "udf7"):
            self.assertIn(key, raw, f"{key} must survive so it can be mapped later")

    def test_array_payload(self):
        second = item(
            skuCode="002IJ-00273230",
            udf2="002IJ002703230",
            size="32 30",
            upc_ean="5401231363523",
        )
        request, _dup = self.ingest([item(), second])
        self.assertEqual(request.item_count, 2)
        self.assertTrue(self.variant("002IJ002703228"))
        self.assertTrue(self.variant("002IJ002703230"))

    def test_item_without_udf2_is_an_error_not_a_guess(self):
        request, _dup = self.ingest([item(udf2=None)])
        staged = request.item_ids
        self.assertEqual(staged.state, "error")
        self.assertIn("udf2", staged.error)
        self.assertFalse(self.template("002IJ-0027"), "nothing may be created from an unkeyed item")

    def test_dry_run_validates_without_writing(self):
        self.env["ir.config_parameter"].sudo().set_param("retail_import.mdm_dry_run", "1")
        try:
            request, _dup = self.ingest([item()])
        finally:
            self.env["ir.config_parameter"].sudo().set_param("retail_import.mdm_dry_run", "0")

        self.assertTrue(request.dry_run)
        self.assertEqual(request.item_ids.state, "skipped")
        self.assertFalse(self.template("002IJ-0027"), "shadow mode must not touch master data")

    def test_failed_request_can_be_replayed(self):
        self.map_sample_category()
        request, _dup = self.ingest([item(udf2=None)])
        self.assertEqual(request.item_ids.state, "error")

        request.item_ids.payload = item()  # the mapping problem is fixed
        request.action_replay()

        self.assertEqual(request.item_ids.state, "done")
        self.assertTrue(self.variant("002IJ002703228"))
