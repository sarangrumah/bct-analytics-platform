# -*- coding: utf-8 -*-
"""Field mapping, and the gates on the fields that can move money.

Two of the incoming fields are not safe to apply on sight:

* ``baseCost`` -> ``standard_price`` posts an inventory revaluation entry on a
  storable product that has stock;
* ``isActive`` -> ``active`` cascades to variants and can break open POS/SO/quant
  references.

Both are therefore recorded always and applied only behind a default-off parameter.
The tests below exist to make sure a future refactor cannot quietly flip that.

The category crosswalk gets the same treatment for a different reason: ``categ_id``
drives the revenue and COGS accounts, and the feed's taxonomy does not match X101's.
An unmapped pair still creates its product -- sales have to be able to post -- but it
is flagged rather than silently filed somewhere plausible.
"""

from __future__ import annotations

from .common import MdmCase, item


class TestMdmMapping(MdmCase):
    def _param(self, name, value):
        self.env["ir.config_parameter"].sudo().set_param(f"retail_import.{name}", value)

    # -- category ---------------------------------------------------------
    def test_unmapped_category_is_derived_and_flagged(self):
        self.ingest([item()])
        template = self.template("002IJ-0027")

        self.assertTrue(template, "the product is still created, so sales can post")
        self.assertTrue(template.mdm_category_unmapped)
        self.assertIn("MENS BOTTOMS", template.categ_id.complete_name.upper())

    def test_crosswalk_pin_wins(self):
        target = self.env["product.category"].create({"name": "Retail Slim Jeans"})
        self.Map.create({"gender": "MEN", "category1": "BOTTOMS", "category2": "LONG BOTTOMS", "categ_id": target.id})
        self.ingest([item()])

        template = self.template("002IJ-0027")
        self.assertEqual(template.categ_id, target)
        self.assertFalse(template.mdm_category_unmapped)

    def test_crosswalk_triple_reuses_the_x101_tree(self):
        self.Map.create(
            {
                "gender": "MEN",
                "category1": "BOTTOMS",
                "category2": "LONG BOTTOMS",
                "x101_category": "MENS BOTTOMS",
                "x101_class": "JEANS",
                "x101_subclass": "SLIM",
            }
        )
        self.ingest([item()])

        template = self.template("002IJ-0027")
        self.assertFalse(template.mdm_category_unmapped)
        self.assertEqual(template.categ_id.name, "SLIM")
        # The category must be the very record the file import would have used.
        xid = self.Executor._safe_xid("cat_l3_", "MENS BOTTOMS_JEANS_SLIM")
        self.assertEqual(self.Executor._xid_get(self.namespace, xid, "product.category"), template.categ_id.id)

    def test_gender_fallback_entry_matches(self):
        target = self.env["product.category"].create({"name": "Any Bottoms"})
        self.Map.create({"category1": "BOTTOMS", "categ_id": target.id})
        self.ingest([item()])
        self.assertEqual(self.template("002IJ-0027").categ_id, target)

    # -- safe fields -------------------------------------------------------
    def test_is_saleable_is_applied(self):
        self.ingest([item(isSaleable="No")])
        self.assertFalse(self.template("002IJ-0027").sale_ok)

    def test_hs_code_written_when_the_field_exists(self):
        self.ingest([item()])
        template = self.template("002IJ-0027")
        if "hs_code" in template._fields:
            self.assertEqual(template.hs_code, "62034290")
        else:
            self.skipTest("stock_delivery not installed; hs_code is soft-probed")

    # -- gated fields ------------------------------------------------------
    def test_cost_is_recorded_but_not_applied_by_default(self):
        self.ingest([item(baseCost="1500")])
        template = self.template("002IJ-0027")
        self.assertEqual(template.mdm_base_cost, 1500.0)
        self.assertEqual(template.standard_price, 0.0, "writing cost can post a revaluation entry")

    def test_cost_is_applied_when_the_gate_is_open(self):
        self._param("mdm_write_cost", "1")
        try:
            self.ingest([item(baseCost="1500")])
        finally:
            self._param("mdm_write_cost", "0")
        self.assertEqual(self.template("002IJ-0027").standard_price, 1500.0)

    def test_is_active_no_does_not_archive_by_default(self):
        self.ingest([item(isActive="No")])
        template = self.template("002IJ-0027")
        self.assertTrue(template.active, "archiving cascades to variants; it needs a decision")
        self.assertFalse(template.mdm_active_flag, "but the flag is recorded for the ops report")

    def test_is_active_no_archives_when_the_gate_is_open(self):
        self._param("mdm_apply_active", "1")
        try:
            self.ingest([item(isActive="No")])
        finally:
            self._param("mdm_apply_active", "0")
        template = self.template("002IJ-0027").with_context(active_test=False)
        self.assertTrue(template.exists())
        self.assertFalse(template.active)

    # -- tracking is create-only -------------------------------------------
    def test_tracking_applied_on_create(self):
        self.ingest([item(serialTrackingRequired="Yes")])
        self.assertEqual(self.template("002IJ-0027").tracking, "serial")

    def test_tracking_change_on_a_known_product_is_flagged_not_forced(self):
        self.map_sample_category()
        self.ingest([item()], key="k1")
        template = self.template("002IJ-0027")
        self.assertEqual(template.tracking, "none")

        request, _dup = self.ingest([item(serialTrackingRequired="Yes", salePrice="1299")], key="k2")
        self.assertEqual(request.item_ids.state, "needs_review")
        self.assertIn("serialTrackingRequired", request.item_ids.error)
        self.assertEqual(template.tracking, "none", "changing tracking would break traceability")
        self.assertEqual(template.list_price, 1299.0, "the rest of the update still applies")

    # -- numeric hardening --------------------------------------------------
    def test_non_finite_numbers_never_reach_a_price(self):
        """float("nan") raises nothing and stores fine — then breaks every sum.

        NaN compares false against everything including itself, so a NaN list_price
        would not show up as an outlier in any report; it would just quietly make
        totals wrong. Non-finite input is treated as unparseable instead, which the
        data-quality check already reports as an invalid price.
        """
        for bad in ("nan", "inf", "-inf", "1e400"):
            with self.subTest(salePrice=bad):
                self.env["retail.mdm.request"].sudo().search([]).unlink()
                request, _dup = self.ingest([item(salePrice=bad, baseCost=bad)], key=f"nan-{bad}")
                template = self.template("002IJ-0027")
                self.assertTrue(template, "the product is still created")
                self.assertEqual(template.list_price, 0.0)
                self.assertEqual(template.mdm_base_cost, 0.0)
                # the invariant that actually matters: it is a real number
                self.assertEqual(template.list_price, template.list_price, "list_price is NaN")

    def test_ordinary_numbers_are_unaffected(self):
        self.ingest([item(salePrice="749900", baseCost="500000")])
        template = self.template("002IJ-0027")
        self.assertEqual(template.list_price, 749900.0)
        self.assertEqual(template.mdm_base_cost, 500000.0)

    # -- size disagreement --------------------------------------------------
    def test_size_disagreeing_with_udf2_is_flagged(self):
        request, _dup = self.ingest([item(size="99 99")])
        self.assertEqual(request.item_ids.state, "needs_review")
        self.assertIn("size", request.item_ids.error)
        self.assertTrue(self.variant("002IJ002703228"), "the SKU is authoritative, so the variant is right")
        self.assertEqual(
            self.template_sizes("002IJ-0027"),
            [("Inseam", ("28",)), ("Size", ("32",))],
            "the split comes from udf2, not from the contradicting size string",
        )
