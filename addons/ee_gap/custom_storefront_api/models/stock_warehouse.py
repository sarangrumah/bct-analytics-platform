# -*- coding: utf-8 -*-
"""Store-locator projection of ``stock.warehouse`` for the headless storefront.

Odoo stays the source of truth for stores (no separate CMS): a warehouse
already carries a partner address and is the stock origin used for
in-store availability / click & collect (spec F10/F11). We add a few
storefront-only fields (geo, opening hours, hero image, publish flag) and a
JSON serializer consumed by ``/storefront/api/stores``.
"""

from __future__ import annotations

from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    custom_storefront_published = fields.Boolean(
        string="Show in Storefront",
        default=False,
        help="Expose this warehouse as a physical store in the headless storefront store locator.",
    )
    custom_store_latitude = fields.Float(string="Store Latitude", digits=(10, 7))
    custom_store_longitude = fields.Float(string="Store Longitude", digits=(10, 7))
    custom_store_hours = fields.Text(
        string="Opening Hours",
        help="Free-text opening hours, one line per day, shown on the store locator.",
    )
    custom_store_image = fields.Binary(string="Store Photo", attachment=True)

    def _storefront_store_image_url(self) -> str | bool:
        self.ensure_one()
        if self.custom_store_image:
            return f"/web/image/stock.warehouse/{self.id}/custom_store_image"
        return False

    def _storefront_serialize(self) -> dict:
        self.ensure_one()
        partner = self.partner_id
        address_parts = [
            partner.street,
            partner.street2,
            partner.city,
            partner.state_id.name if partner.state_id else None,
            partner.zip,
        ]
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "address": ", ".join(p for p in address_parts if p),
            "street": partner.street or "",
            "city": partner.city or "",
            "zip": partner.zip or "",
            "phone": partner.phone or partner.mobile or "",
            "lat": self.custom_store_latitude or None,
            "lng": self.custom_store_longitude or None,
            "hours": self.custom_store_hours or "",
            "image": self._storefront_store_image_url(),
        }
