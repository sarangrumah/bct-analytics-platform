# -*- coding: utf-8 -*-
"""Headless storefront wishlist (spec F2).

Odoo CE has no clean headless wishlist, so we keep a thin per-partner
``custom.wishlist`` join: one row per (customer, product template), with an
optional variant. All access is partner-scoped in the customer API — the
request never supplies the partner id.
"""

from __future__ import annotations

from odoo import api, fields, models


class CustomWishlist(models.Model):
    _name = "custom.wishlist"  # nosemgrep
    _description = "Storefront Wishlist Entry"
    _order = "create_date desc"

    partner_id = fields.Many2one("res.partner", string="Customer", required=True, ondelete="cascade", index=True)
    product_tmpl_id = fields.Many2one("product.template", string="Product", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Variant", ondelete="cascade")

    _partner_product_uniq = models.Constraint(
        "unique(partner_id, product_tmpl_id)",
        "This product is already in the wishlist.",
    )

    @api.model
    def _storefront_add(self, partner, product_tmpl_id, product_id=None):
        """Idempotent add — returns the (existing or new) wishlist row."""
        tmpl = self.env["product.template"].sudo().browse(int(product_tmpl_id))
        if not tmpl.exists() or not tmpl.sale_ok:
            return self.browse()
        entry = self.sudo().search(
            [("partner_id", "=", partner.id), ("product_tmpl_id", "=", tmpl.id)],
            limit=1,
        )
        if entry:
            return entry
        return self.sudo().create(
            {
                "partner_id": partner.id,
                "product_tmpl_id": tmpl.id,
                "product_id": int(product_id) if product_id else (tmpl.product_variant_id.id or False),
            }
        )

    def _storefront_serialize(self, pricelist=None) -> dict:
        """Return the product card plus the wishlist row id, for the storefront."""
        self.ensure_one()
        data = self.product_tmpl_id._storefront_serialize(detail=False, pricelist=pricelist)
        data["wishlist_id"] = self.id
        return data
