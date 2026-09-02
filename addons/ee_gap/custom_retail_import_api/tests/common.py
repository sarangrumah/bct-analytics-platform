# -*- coding: utf-8 -*-
"""Shared fixtures for the MDM API suite.

The controller is exercised through the models rather than over HTTP: the transport
layer (auth, envelope, status codes) belongs to ``@secure_endpoint`` and is tested in
``custom_core``, while what is worth pinning here is the mapping and the decisions —
which item becomes a product, which is refused, and why.
"""

from __future__ import annotations

from odoo.tests.common import TransactionCase

#: The sample item from the apparel brand Principal, verbatim.
SAMPLE = {
    "skuCode": "002IJ-00273228",
    "skuName": "BLR MB 5PKT 555 ZLATAN",
    "classification": "Normal",
    "detailDesc": "BLR MB 5PKT 555 ZLATAN",
    "length": 0,
    "width": 0,
    "height": 0,
    "weight": "",
    "size": "32 28",
    "brand": "RETAIL",
    "salePrice": "999",
    "vendorCode": "LS",
    "baseCost": "999",
    "taxCategory": "62034290",
    "isActive": "Yes",
    "serialTrackingRequired": "No",
    "isSaleable": "Yes",
    "budf3": "A",
    "udf1": "002IJ-0027",
    "udf2": "002IJ002703228",
    "udf3": "00054",
    "udf4": "Fall",
    "udf5": "1004",
    "udf6": "2003",
    "udf7": "001",
    "udf8": "MEN",
    "udf10": "SEASONAL FASHION",
    "category1": "BOTTOMS",
    "category2": "LONG BOTTOMS",
    "upc_ean": "5401231363516",
}


def item(**overrides):
    """A copy of the sample with fields replaced (or removed, by passing None)."""
    payload = dict(SAMPLE)
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


class MdmCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # queue_job runs with_delay() inline under this key, so a staged request is
        # processed in the same transaction -- the same code path as production, just
        # without the worker round-trip.
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.Request = cls.env["retail.mdm.request"]
        cls.Item = cls.env["retail.mdm.item"]
        cls.Map = cls.env["retail.mdm.category.map"]
        cls.Product = cls.env["product.product"]
        cls.Template = cls.env["product.template"]
        cls.Executor = cls.env["retail.import.executor"]
        cls.namespace = cls.env["retail.mdm.processor"]._namespace()

    def ingest(self, items, key="k1"):
        """Stage and process a batch synchronously, returning the request."""
        request, duplicate = self.Request.ingest(items, key, source_ip="10.0.0.5")
        return request, duplicate

    def variant(self, sku):
        return self.Product.search([("default_code", "=", sku)], limit=1)

    def map_sample_category(self):
        """Give the sample item a crosswalk entry.

        Without one an item is legitimately ``needs_review`` -- the feed's
        BOTTOMS/LONG BOTTOMS pair is not an X101 category. Tests that are about
        something else call this so the item can reach ``done``.
        """
        return self.Map.create(
            {
                "gender": "MEN",
                "category1": "BOTTOMS",
                "category2": "LONG BOTTOMS",
                "x101_category": "MENS BOTTOMS",
                "x101_class": "JEANS",
                "x101_subclass": "SLIM",
            }
        )

    def template_sizes(self, code):
        """The Size/Inseam values carried by a template's attribute lines.

        Asserting on the *variant* would be wrong: Odoo leaves single-value
        attributes out of a variant's combination, so the sample item -- one size,
        one inseam -- produces a variant with no attribute values at all.
        """
        template = self.template(code)
        return sorted(
            (line.attribute_id.name, tuple(sorted(line.value_ids.mapped("name"))))
            for line in template.attribute_line_ids
        )

    def template(self, code):
        """Resolve a template by its mainline code, via the external ID.

        Not by ``default_code``: Odoo mirrors a lone variant's code into its template,
        and the sample item (one size, one inseam) produces exactly one variant, so
        searching ``default_code == "002IJ-0027"`` finds nothing.
        """
        xid = self.Executor._safe_xid("tmpl_", code)
        template_id = self.Executor._xid_get(self.namespace, xid, "product.template")
        return self.Template.browse(template_id) if template_id else self.Template.browse()
