# -*- coding: utf-8 -*-
"""Alternate barcodes for a product variant.

A variant (``product.product``) has ONE native ``barcode`` (the primary, e.g. the
current/latest GTIN). Retail master data often carries several GTINs for the same
physical item (historical re-issues, packaging levels, vendor vs internal codes).
Stock is tracked per variant (confirmed by the apparel brand X32P movement report, which
keys on store + product code + waist + inseam and has no GTIN column), so these
extra GTINs are *alternate barcodes of the same stock unit*, not separate items.

This model stores those alternates as ``product.barcode`` rows linked to the
variant, and ``product.product._resolve_barcode`` matches a scanned code against
the primary first, then the alternates — so every GTIN scans to the same variant
with a single shared inventory.
"""

from __future__ import annotations

from odoo import api, fields, models


class ProductBarcode(models.Model):
    _name = "product.barcode"
    _description = "Alternate Product Barcode"
    _order = "product_id, id"

    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    barcode = fields.Char(required=True, index=True)
    note = fields.Char(help="Origin/type, e.g. 'GTIN historis' or 'carton'.")
    active = fields.Boolean(default=True)

    # One row per (product, barcode). Non-unique globally on barcode by design:
    # source data may reuse a GTIN; resolution picks the primary first, then a
    # single alternate. (Odoo 19 API — _sql_constraints is ignored.)
    _uniq_product_barcode = models.Constraint(
        "unique(product_id, barcode)",
        "This barcode is already registered for this product.",
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    barcode_ids = fields.One2many("product.barcode", "product_id", string="Alternate Barcodes")

    @api.model
    def _resolve_barcode(self, code):
        """Return the variant for a scanned code: primary ``barcode`` first, then
        any alternate ``product.barcode``. Empty recordset if none match."""
        if not code:
            return self.browse()
        product = self.search([("barcode", "=", code)], limit=1)
        if not product:
            alt = self.env["product.barcode"].search([("barcode", "=", code)], limit=1)
            product = alt.product_id
        return product
