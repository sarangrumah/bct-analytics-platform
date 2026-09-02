# -*- coding: utf-8 -*-
"""The four ways an MDM message can collide with something that already exists.

Each case has a different right answer, and three of the four are cases where the
tempting behaviour is wrong:

(a) the same message twice     -> a duplicate; answer with the original, do nothing
(b) the same SKU, new content  -> NOT a duplicate; a legitimate update
(c) a GTIN owned by another    -> refuse; never steal a barcode
(d) a template-code collision  -> adopt, or upgrade in place, or refuse — never fork
"""

from __future__ import annotations

from odoo import fields

from .common import MdmCase, item


class TestMdmDuplicates(MdmCase):
    # -- (a) the same message twice --------------------------------------
    def test_same_message_twice_is_one_request(self):
        first, dup_first = self.ingest([item()], key="same-key")
        second, dup_second = self.ingest([item()], key="same-key")

        self.assertFalse(dup_first)
        self.assertTrue(dup_second, "a re-POST after a timed-out response is not an error")
        self.assertEqual(first, second, "the original request is returned, not a new one")
        self.assertEqual(self.Request.search_count([("dedupe_key", "=", "same-key")]), 1)

    def test_dedupe_is_per_key_not_per_content(self):
        """An explicit X-Request-Id lets the sender retry a body it already changed."""
        self.ingest([item()], key="req-1")
        request, duplicate = self.ingest([item()], key="req-2")
        self.assertFalse(duplicate)
        self.assertEqual(request.item_ids.state, "duplicate", "content unchanged, so no writes")

    # -- (b) the same SKU in a later message ------------------------------
    def test_unchanged_content_is_a_no_op(self):
        self.ingest([item()], key="k1")
        template = self.template("002IJ-0027")
        before = template.mdm_content_hash

        request, _dup = self.ingest([item()], key="k2")
        self.assertEqual(request.item_ids.state, "duplicate")
        self.assertEqual(template.mdm_content_hash, before)

    def test_changed_content_updates(self):
        self.map_sample_category()
        self.ingest([item()], key="k1")
        request, _dup = self.ingest([item(salePrice="1299")], key="k2")

        self.assertEqual(request.item_ids.state, "done")
        self.assertEqual(self.template("002IJ-0027").list_price, 1299.0)
        self.assertEqual(
            self.Template.search_count([("mdm_template_code", "=", "002IJ-0027")]),
            1,
            "an update must not fork a second template",
        )

    def test_stale_message_does_not_revert_a_newer_one(self):
        """MQ does not guarantee order; a retried old message must not undo a new one."""
        self.ingest([item(salePrice="1299")], key="k1")
        template = self.template("002IJ-0027")
        template.mdm_synced_at = fields.Datetime.add(fields.Datetime.now(), hours=1)

        request, _dup = self.ingest([item(salePrice="999")], key="k2")
        self.assertEqual(request.item_ids.state, "skipped")
        self.assertIn("Stale", request.item_ids.error)
        self.assertEqual(template.list_price, 1299.0, "the newer price must survive")

    # -- (c) GTIN ownership ------------------------------------------------
    def test_gtin_owned_by_another_variant_is_refused(self):
        other = self.Product.create({"name": "Someone else", "default_code": "OTHER-SKU", "barcode": "5401231363516"})
        request, _dup = self.ingest([item()])
        staged = request.item_ids

        self.assertEqual(staged.state, "conflict")
        self.assertIn("5401231363516", staged.error)
        self.assertEqual(other.barcode, "5401231363516", "the other product must be untouched")
        self.assertFalse(self.variant("002IJ002703228"), "and nothing created for the incoming one")

    def test_second_gtin_for_the_same_sku_accumulates(self):
        """One upc_ean per message, many GTINs per SKU: aliases add up, never overwrite."""
        self.ingest([item()], key="k1")
        self.ingest([item(upc_ean="5401231399123")], key="k2")

        variant = self.variant("002IJ002703228")
        resolvable = {variant.barcode} | set(variant.barcode_ids.mapped("barcode"))
        self.assertIn("5401231363516", resolvable)
        self.assertIn("5401231399123", resolvable)
        self.assertEqual(variant.barcode, "5401231363516", "the first GTIN stays primary")

    # -- (d) template-code collisions --------------------------------------
    def test_existing_template_without_an_external_id_is_adopted(self):
        existing = self.Template.create({"name": "Loaded by script", "default_code": "002IJ-0027"})
        self.ingest([item()])

        adopted = self.Executor._xid_get(
            self.namespace, self.Executor._safe_xid("tmpl_", "002IJ-0027"), "product.template"
        )
        self.assertEqual(adopted, existing.id, "the API must not create a second template")
        self.assertEqual(
            self.Template.search_count([("mdm_template_code", "=", "002IJ-0027")]),
            1,
            "exactly one template owns this mainline code",
        )
        self.assertEqual(existing.product_variant_ids.mapped("default_code"), ["002IJ002703228"])

    def test_x24_stub_is_upgraded_in_place(self):
        """The join between the sales-side and master-side halves of this feature.

        A stub created by X24DN already has posted pos.order.line rows pointing at it,
        so the upgrade has to keep the same record id -- creating a fresh template and
        leaving the stub behind would strand that revenue on a placeholder product.
        """
        stub_template = self.Template.create(
            {
                "name": "BLR MB 5PKT (from sales)",
                "default_code": "002IJ002703228",
                "mdm_pending": True,
                "mdm_source": "x24_autoregister",
            }
        )
        stub = stub_template.product_variant_id
        self.Executor._xid_set(
            self.namespace,
            self.Executor._safe_xid("x24prod_", "002IJ002703228"),
            "product.product",
            stub.id,
        )

        self.ingest([item()])

        stub_template.invalidate_recordset()
        stub.invalidate_recordset()
        self.assertTrue(stub.exists(), "the product id must survive; posted POS lines point at it")
        self.assertEqual(stub.default_code, "002IJ002703228", "still keyed on the PROD SKU")
        self.assertEqual(stub_template.mdm_template_code, "002IJ-0027", "now carries the mainline code")
        self.assertFalse(stub_template.mdm_pending)
        self.assertEqual(stub_template.mdm_source, "mdm_api")
        self.assertEqual(
            self.Executor._xid_get(self.namespace, self.Executor._safe_xid("tmpl_", "002IJ-0027"), "product.template"),
            stub_template.id,
            "the template xid must point at the upgraded stub, not a new record",
        )

    def test_safe_xid_collision_is_refused(self):
        """``_safe_xid`` maps every non-alphanumeric to '_', so two codes can collide.

        Rare, but silently merging two different articles into one template would be
        unrecoverable, so it is reported instead.
        """
        self.ingest([item()], key="k1")
        request, _dup = self.ingest([item(udf1="002IJ.0027")], key="k2")

        self.assertEqual(request.item_ids.state, "conflict")
        self.assertIn("002IJ", request.item_ids.error)
