# -*- coding: utf-8 -*-
"""JSON projections of the catalog for the headless storefront.

Kept on the model (not the controller) so both the public API and the BFF
cache-warming endpoint serialize products identically.
"""

from __future__ import annotations

from datetime import timedelta

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    custom_material_composition = fields.Text(
        string="Material & Composition",
        translate=True,
        help="Fabric / material composition shown on the storefront product page (ID/EN). "
        "Plain text (per-language) — translated independently like the product name.",
    )
    custom_drop_date = fields.Date(
        string="Drop / Launch Date",
        help="When this product was launched. Drives the automatic 'New' badge "
        "(within the storefront 'new' window). Falls back to the creation date "
        "when empty.",
    )
    custom_is_new = fields.Boolean(
        string="New (storefront)",
        compute="_compute_custom_is_new",
        help="Automatically true when the drop/launch (or creation) date is within "
        "the storefront 'new' window (ir.config_parameter "
        "custom_storefront_api.new_window_days, default 30).",
    )

    def _storefront_new_window_days(self) -> int:
        val = self.env["ir.config_parameter"].sudo().get_param("custom_storefront_api.new_window_days", "30")
        try:
            return int(val)
        except (TypeError, ValueError):
            return 30

    @api.depends("custom_drop_date", "create_date")
    def _compute_custom_is_new(self):
        cutoff = fields.Date.context_today(self) - timedelta(days=self._storefront_new_window_days())
        for product in self:
            effective = product.custom_drop_date or (product.create_date.date() if product.create_date else False)
            product.custom_is_new = bool(effective and effective >= cutoff)

    def _storefront_image_urls(self) -> dict:
        self.ensure_one()
        return {
            "thumb": f"/web/image/product.template/{self.id}/image_256",
            "medium": f"/web/image/product.template/{self.id}/image_512",
            "large": f"/web/image/product.template/{self.id}/image_1024",
        }

    def _storefront_in_stock(self) -> bool:
        # Storable products: gate on availability; services/consumables: always sellable.
        try:
            if self.is_storable:
                return (self.qty_available or 0.0) > 0
        except Exception:  # field name varies across editions — fail open
            pass
        return True

    def _storefront_tags(self):
        # ``product.tag`` (built-in) drives both UI facets and AI search (spec F5).
        self.ensure_one()
        return self.product_tag_ids if "product_tag_ids" in self._fields else self.browse()

    def _storefront_store_availability(self) -> list:
        """Per-store on-hand availability (spec F11: in-store stock / click&collect).

        Reads ``qty_available`` scoped to each published warehouse's stock
        location, so the storefront can show "Available at [store]".
        """
        self.ensure_one()
        variant = self.product_variant_id
        warehouses = (
            self.env["stock.warehouse"].sudo().search([("custom_storefront_published", "=", True)], order="name")
        )
        rows = []
        for wh in warehouses:
            qty = (
                variant.with_context(location=wh.lot_stock_id.id).qty_available if variant and wh.lot_stock_id else 0.0
            )
            rows.append(
                {
                    "store_id": wh.id,
                    "store_name": wh.name,
                    "city": wh.partner_id.city or "",
                    "qty": qty,
                    "in_stock": qty > 0,
                }
            )
        return rows

    @api.model
    def _storefront_pricelist(self):
        # Storefront sells against a single active pricelist (no per-customer
        # pricelist selection on the headless side). Resolve it once and pass
        # into ``_storefront_serialize`` so a PLP page doesn't re-search per row.
        return self.env["product.pricelist"].sudo().search([], order="id", limit=1)

    def _storefront_price_info(self, pricelist=None) -> dict:
        """Selling price + strike-through original when a pricelist discounts it (spec F4)."""
        self.ensure_one()
        list_price = self.list_price or 0.0
        price = list_price
        if pricelist:
            try:
                price = pricelist._get_product_price(self.product_variant_id or self, 1.0)
            except Exception:  # pricing edge cases — fall back to catalog price
                price = list_price
        discounted = bool(list_price) and price < (list_price - 0.01)
        return {
            "price": price,
            "compare_at": list_price if discounted else None,
            "discount_pct": round((1 - price / list_price) * 100) if discounted else 0,
        }

    def _storefront_serialize(self, detail: bool = False, pricelist=None) -> dict:
        self.ensure_one()
        imgs = self._storefront_image_urls()
        if pricelist is None:
            pricelist = self._storefront_pricelist()
        price_info = self._storefront_price_info(pricelist)
        vals = {
            "id": self.id,
            "name": self.name,
            "slug": str(self.id),
            "price": price_info["price"],
            "compare_at": price_info["compare_at"],
            "discount_pct": price_info["discount_pct"],
            "currency": (self.currency_id.name if self.currency_id else "IDR"),
            "summary": self.description_sale or "",
            "image": imgs["medium"],
            "ref": self.default_code or "",
            "is_new": self.custom_is_new,
            "categories": [{"id": c.id, "name": c.name} for c in self.public_categ_ids],
            "tags": [{"id": t.id, "name": t.name} for t in self._storefront_tags()],
            "in_stock": self._storefront_in_stock(),
        }
        if detail:
            gallery = [imgs["large"]]
            for img in self.product_template_image_ids:
                gallery.append(f"/web/image/product.image/{img.id}/image_1024")
            vals.update(
                {
                    "images": gallery,
                    "description": self.website_description or self.description_sale or "",
                    "material": self.custom_material_composition or "",
                    "variants": [
                        {
                            "id": v.id,
                            "price": v.lst_price,
                            "in_stock": v._storefront_variant_in_stock(),
                            "attributes": [
                                {
                                    "attribute": a.attribute_id.name,
                                    "value": a.name,
                                }
                                for a in v.product_template_attribute_value_ids
                            ],
                        }
                        for v in self.product_variant_ids
                    ],
                }
            )
        return vals


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _storefront_variant_in_stock(self) -> bool:
        self.ensure_one()
        try:
            if self.is_storable:
                return (self.qty_available or 0.0) > 0
        except Exception:
            pass
        return True
