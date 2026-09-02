# -*- coding: utf-8 -*-
"""Make the X101 "PRODUCT CODE" notation resolve to a variant.

X101 ships two codes for the same physical item:

==========================  ==================  ======================
column                      example             used by
==========================  ==================  ======================
``PROD SKU``                ``000A900050OS``    XStore, and Odoo's
                                                ``default_code``
``PRODUCT CODE``            ``000A9-0005OS``    ordering from the
  + ``ITEM SIZE``                               supplier
==========================  ==================  ======================

Both are in real use, so neither can be dropped: the sized hyphen form is what the
supplier is ordered with, the PROD SKU is what XStore and the ledger key on.

``PROD SKU`` is what the importer writes to ``product.product.default_code``,
because that is the level stock is tracked at. Verified over all 214.305 X101
rows, the two are related by a single rule::

    PROD SKU = PRODUCT CODE without the hyphen + "0" + ITEM SIZE + INSEAM

Downstream systems (purchase orders, vendor sheets) quote the hyphenated form
with the size glued on -- ``000A9-0005OS``, ``00501-00002830`` -- which matches
neither ``default_code`` nor ``barcode``, so an XLSX PO import dies with
"No matching record found for name '000A9-0005OS' in field 'Order Lines/Product'".

Rewriting ``default_code`` into the hyphenated form is not an option: X24/X48/X70D
matching, the stock reports and every posted document key on the PROD SKU. So the
translation happens at lookup time instead -- ``name_search`` only reaches this
code after the native search found nothing, so nothing that resolves today can
change meaning.
"""

from __future__ import annotations

import re

from odoo import api, fields, models
from odoo.fields import Domain

# style(5) "-" colour(4) then the optional size/inseam tail, e.g. 000A9-0005OS.
_X101_HYPHEN_RE = re.compile(r"^([0-9A-Za-z]{5})-([0-9A-Za-z]{4})([0-9A-Za-z]*)$")
# the same shape on the PROD SKU side, with X101's literal "0" separator.
_X101_SKU_RE = re.compile(r"^([0-9A-Za-z]{5})([0-9A-Za-z]{4})0([0-9A-Za-z]*)$")


def _x101_product_code_from_sku(value):
    """``000A900050OS`` -> ``000A9-0005OS``. None when *value* is not a PROD SKU."""
    if not value:
        return None
    match = _X101_SKU_RE.match(str(value).strip())
    if not match:
        return None
    style, colour, tail = match.groups()
    return f"{style}-{colour}{tail}"


def _x101_sku_from_product_code(value):
    """``000A9-0005OS`` -> ``000A900050OS``. None when *value* is not that shape.

    The literal ``0`` between colour and size is X101's own separator, present in
    every PROD SKU in the master file; it is not padding we may drop.
    """
    if not value:
        return None
    match = _X101_HYPHEN_RE.match(str(value).strip())
    if not match:
        return None
    style, colour, tail = match.groups()
    return f"{style}{colour}0{tail}".upper()


class ProductProduct(models.Model):
    _inherit = "product.product"

    x101_product_code = fields.Char(
        string="Supplier Order Code",
        compute="_compute_x101_product_code",
        search="_search_x101_product_code",
        help="The hyphenated X101 PRODUCT CODE (+ size/inseam) used when ordering from "
        "the supplier. Derived from the PROD SKU in Internal Reference, so it never "
        "drifts from it and costs no extra column.",
    )

    @api.depends("default_code")
    def _compute_x101_product_code(self):
        for product in self:
            product.x101_product_code = _x101_product_code_from_sku(product.default_code)

    def _search_x101_product_code(self, operator, value):
        """Search the hyphenated code by translating it back to the PROD SKU."""
        if operator in ("=", "!=", "ilike", "not ilike", "like", "not like", "=like"):
            sku = _x101_sku_from_product_code(value)
            if sku:
                return Domain("default_code", operator, sku)
        return Domain("default_code", operator, value)

    @api.model
    def name_search(self, name="", domain=None, operator="ilike", limit=100):
        results = super().name_search(name, domain, operator, limit)
        if results or not name or operator in ("!=", "not ilike", "not like"):
            return results

        # 1. the hyphenated X101 PRODUCT CODE + size
        sku = _x101_sku_from_product_code(name)
        if sku:
            results = super().name_search(sku, domain, operator, limit)
            if results:
                return results

            # A mainline code with no size tail (``000A9-0005``) only identifies a
            # variant when the style/colour has exactly one. Anything ambiguous is
            # left unresolved on purpose -- silently picking a size would book the
            # PO against the wrong item.
            if len(sku) == 10:
                candidates = super().name_search(f"{sku}%", domain, "=like", 2)
                if len(candidates) == 1:
                    return candidates

        # 2. an alternate GTIN (custom_product_barcode), which the native
        #    name_search does not look at -- it only reads the primary barcode.
        product = self._resolve_barcode(str(name).strip())
        if product:
            product = product.filtered_domain(domain) if domain else product
            if product:
                return [(product.id, product.sudo().display_name)]

        return results
