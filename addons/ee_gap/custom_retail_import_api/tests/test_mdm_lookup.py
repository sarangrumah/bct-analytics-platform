# -*- coding: utf-8 -*-
"""The lookup surface XStore/MDM uses to ask "do you have this SKU?".

The property that matters is agreement: the lookup must resolve a SKU exactly when the
X24DN importer would. If the API said "registered" for something ``resolve_product``
cannot find, the caller would send sales that then park — which is the failure this
endpoint exists to prevent.

The resolution order is therefore the same one, in the same sequence, and the tests
below walk each rung of it.
"""

from __future__ import annotations

from .common import MdmCase, item


class TestMdmLookup(MdmCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pending = cls.env["retail.mdm.pending.sku"]

    def _resolve(self, *, ean=None, sku_code=None, sku=None):
        """The controller's resolution order, exercised against the same models."""
        Product = self.Product
        product = Product.browse()
        if ean:
            product = Product._resolve_barcode(ean)
        if not product and sku_code:
            product = Product.search([("mdm_sku_code", "=", sku_code)], limit=1)
        if not product and sku:
            product = Product.search([("default_code", "=", sku)], limit=1)
        return product

    def test_resolves_by_every_key_the_importer_uses(self):
        self.ingest([item()])
        expected = self.variant("002IJ002703228")

        self.assertEqual(self._resolve(ean="5401231363516"), expected)
        self.assertEqual(self._resolve(sku_code="002IJ-00273228"), expected)
        self.assertEqual(self._resolve(sku="002IJ002703228"), expected)

    def test_resolves_by_a_secondary_gtin(self):
        self.ingest([item()], key="k1")
        self.ingest([item(upc_ean="5401231399123")], key="k2")
        expected = self.variant("002IJ002703228")
        self.assertEqual(self._resolve(ean="5401231399123"), expected)

    def test_unknown_sku_resolves_to_nothing(self):
        self.assertFalse(self._resolve(sku="NOPE-0001"))

    def test_pending_entry_is_discoverable_for_an_unregistered_sku(self):
        """ "Not found" is more useful when it can say "but we have seen it in sales"."""
        self.Pending._record(
            {
                "item_code": "ZT9YB00010",
                "waist": "34",
                "inseam": "10",
                "ean": "9991231300001",
                "item_description": "501 ORIGINAL",
                "store_code": "S001",
            }
        )
        self.assertFalse(self._resolve(sku="ZT9YB000103410"))

        found = self.Pending.search(
            ["|", ("composite_code", "in", ["ZT9YB000103410"]), ("ean", "=", "9991231300001")],
            limit=1,
        )
        self.assertTrue(found)
        self.assertEqual(found.state, "pending")
        self.assertEqual(found.occurrence_count, 1)

    def test_pending_list_is_ordered_by_impact(self):
        for index in range(3):
            row = {
                "item_code": f"ZTPEN{index:05d}",
                "waist": "",
                "inseam": "",
                "ean": f"999123130000{index}",
            }
            for _ in range(index + 1):
                self.Pending._record(row)

        rows = self.Pending.search([("state", "=", "pending")])
        counts = rows.mapped("occurrence_count")
        self.assertEqual(counts, sorted(counts, reverse=True), "noisiest SKUs first")
