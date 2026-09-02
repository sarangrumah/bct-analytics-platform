# -*- coding: utf-8 -*-
"""Cart + checkout helpers for the headless storefront.

A storefront cart is just a ``website_sale`` draft ``sale.order`` keyed to
the customer's partner. We reuse Odoo's own ``_cart_add`` /
``_cart_update_line_quantity`` so pricelists, taxes and combo/variant rules
all compute natively — we never hand-roll order lines. Shipping reuses
``custom_ecommerce``'s ``delivery.carrier.id_rate_shipment``.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    custom_is_pickup = fields.Boolean(
        string="Click & Collect",
        help="True when the order is collected in-store (warehouse_id = the chosen store) "
        "instead of shipped by a carrier.",
    )

    # -------- Cart resolution --------

    @api.model
    def _storefront_website(self):
        return self.env["website"].sudo().search([], limit=1, order="id")

    @api.model
    def _storefront_get_or_create_cart(self, partner):
        """Return the partner's open cart (draft website order), creating one."""
        website = self._storefront_website()
        Order = self.sudo()
        cart = Order.search(
            [
                ("partner_id", "=", partner.id),
                ("state", "=", "draft"),
                ("website_id", "!=", False),
            ],
            limit=1,
            order="id desc",
        )
        if not cart:
            cart = Order.with_context(website_id=website.id if website else None).create(
                {
                    "partner_id": partner.id,
                    "website_id": website.id if website else False,
                }
            )
        return cart

    def _storefront_ctx(self):
        """Context website_sale's cart helpers expect."""
        self.ensure_one()
        return self.sudo().with_context(website_id=self.website_id.id if self.website_id else None)

    # -------- Cart mutations (delegate to website_sale) --------

    def _storefront_add_line(self, product_id, qty=1.0):
        self.ensure_one()
        if qty <= 0:
            raise ValidationError(_("Quantity must be positive."))
        product = self.env["product.product"].sudo().browse(int(product_id))
        if not product.exists():
            # Allow passing a product.template id too — resolve to its first variant.
            tmpl = self.env["product.template"].sudo().browse(int(product_id))
            if not tmpl.exists() or not tmpl.product_variant_id:
                raise ValidationError(_("Unknown product %s.") % product_id)
            product = tmpl.product_variant_id
        self._storefront_ctx()._cart_add(product_id=product.id, quantity=qty)
        return self

    def _storefront_set_line_qty(self, line_id, qty):
        self.ensure_one()
        line = self.order_line.filtered(lambda l: l.id == int(line_id))
        if not line:
            raise ValidationError(_("Cart line %s not found.") % line_id)
        self._storefront_ctx()._cart_update_line_quantity(line_id=line.id, quantity=max(qty, 0.0))
        return self

    def _storefront_remove_line(self, line_id):
        return self._storefront_set_line_qty(line_id, 0.0)

    # -------- Shipping --------

    def _storefront_apply_shipping(self, carrier_id):
        self.ensure_one()
        carrier = self.env["delivery.carrier"].sudo().browse(int(carrier_id))
        if not carrier.exists():
            raise ValidationError(_("Unknown carrier %s.") % carrier_id)
        rate = carrier.id_rate_shipment(self)
        if not rate.get("success"):
            raise UserError(rate.get("error_message") or _("Shipping quote failed."))
        # Switching to home delivery cancels any in-store pickup selection.
        if self.custom_is_pickup:
            self.sudo().write(
                {
                    "custom_is_pickup": False,
                    "warehouse_id": self._storefront_default_warehouse().id,
                }
            )
        self.sudo().set_delivery_line(carrier, rate["price"])
        return self

    # -------- Click & Collect (pickup in store) --------

    @api.model
    def _storefront_default_warehouse(self):
        """The fulfilment warehouse for home delivery (a non-store warehouse)."""
        WH = self.env["stock.warehouse"].sudo()
        return WH.search([("custom_storefront_published", "=", False)], order="id", limit=1) or WH.search(
            [], order="id", limit=1
        )

    def _storefront_set_pickup(self, warehouse_id):
        """Mark the cart for in-store collection at a published store warehouse."""
        self.ensure_one()
        wh = self.env["stock.warehouse"].sudo().browse(int(warehouse_id))
        if not wh.exists() or not wh.custom_storefront_published:
            raise ValidationError(_("Unknown or non-published store %s.") % warehouse_id)
        # Drop any home-delivery line + carrier (no shipping fee for pickup).
        self.order_line.filtered("is_delivery").sudo().unlink()
        self.sudo().write(
            {
                "custom_is_pickup": True,
                "warehouse_id": wh.id,
                "carrier_id": False,
            }
        )
        return self

    def _storefront_set_shipping_address(self, addr):
        """Create/update a delivery address (child of the cart partner) + set it.

        Works for guests (their only address) and registered customers alike;
        reuses a single storefront delivery child so the cart doesn't accrue
        duplicate addresses.
        """
        self.ensure_one()
        addr = addr or {}

        def cap(v, n=128):
            return (str(v or "").strip())[:n]

        street, city = cap(addr.get("street")), cap(addr.get("city"))
        if not street or not city:
            raise ValidationError(_("Street and city are required."))
        parent = self.partner_id
        country = self.env.ref("base.id", raise_if_not_found=False)  # Indonesia
        vals = {
            "name": cap(addr.get("name") or parent.name) or parent.name,
            "type": "delivery",
            "parent_id": parent.id,
            "street": street,
            "street2": cap(addr.get("street2")) or False,
            "city": city,
            "zip": cap(addr.get("zip"), 16) or False,
            "phone": cap(addr.get("phone") or parent.phone, 32) or False,
            "country_id": country.id if country else False,
        }
        Partner = self.env["res.partner"].sudo()
        child = Partner.search([("parent_id", "=", parent.id), ("type", "=", "delivery")], limit=1)
        if child:
            child.write(vals)
        else:
            child = Partner.create(vals)
        self.sudo().write({"partner_shipping_id": child.id})
        return child

    @api.model
    def _storefront_shipping_quotes(self, order):
        """Return [{carrier_id, name, price, cod_supported, etd}] for all ID carriers."""
        carriers = self.env["delivery.carrier"].sudo().search([("x_id_courier_id", "!=", False)])
        quotes = []
        for carrier in carriers:
            try:
                rate = carrier.id_rate_shipment(order)
            except Exception:  # noqa: BLE001 — one bad carrier must not sink the list
                continue
            if not rate.get("success"):
                continue
            extra = rate.get("id_rate") or {}
            quotes.append(
                {
                    "carrier_id": carrier.id,
                    "name": carrier.name,
                    "price": rate["price"],
                    "currency": extra.get("currency", "IDR"),
                    "etd_days": extra.get("etd_days"),
                    "cod_supported": carrier.x_id_cod_supported,
                    "service": extra.get("service"),
                }
            )
        return quotes

    # -------- Checkout --------

    def _storefront_owned_address(self, address_id):
        """Resolve an address id, asserting it belongs to the cart's partner.

        Prevents IDOR: a customer must not point their order at (and thus read
        back via the cart) another partner's address.
        """
        self.ensure_one()
        addr = self.env["res.partner"].sudo().browse(int(address_id))
        owner = self.partner_id.commercial_partner_id
        if not addr.exists() or (
            addr != self.partner_id
            and addr.parent_id.commercial_partner_id != owner
            and addr.commercial_partner_id != owner
        ):
            raise ValidationError(_("Address %s is not yours.") % address_id)
        return addr

    def _storefront_checkout(
        self,
        shipping_address_id=None,
        billing_address_id=None,
        carrier_id=None,
        pickup_warehouse_id=None,
        shipping_address=None,
    ):
        self.ensure_one()
        if not self.order_line.filtered(lambda l: not l.display_type and not l.is_delivery):
            raise UserError(_("Cart is empty."))
        vals = {}
        if shipping_address_id:
            vals["partner_shipping_id"] = self._storefront_owned_address(shipping_address_id).id
        if billing_address_id:
            vals["partner_invoice_id"] = self._storefront_owned_address(billing_address_id).id
        if vals:
            self.sudo().write(vals)
        # Pickup (click & collect) and carrier shipping are mutually exclusive.
        if pickup_warehouse_id:
            self._storefront_set_pickup(pickup_warehouse_id)
        else:
            # Inline address (typically a guest's) — set before rating the carrier
            # so id_rate_shipment sees the real destination zip.
            if shipping_address:
                self._storefront_set_shipping_address(shipping_address)
            if carrier_id:
                self._storefront_apply_shipping(carrier_id)
        self.sudo().action_confirm()
        return self

    # -------- Serialization --------

    def _storefront_serialize(self) -> dict:
        self.ensure_one()
        lines = []
        for l in self.order_line:
            if l.display_type:
                continue
            lines.append(
                {
                    "id": l.id,
                    "product_id": l.product_id.id,
                    "name": l.product_id.display_name,
                    "qty": l.product_uom_qty,
                    "price_unit": l.price_unit,
                    "price_subtotal": l.price_subtotal,
                    "is_delivery": l.is_delivery,
                    "image": f"/web/image/product.product/{l.product_id.id}/image_128",
                }
            )
        return {
            "order_id": self.id,
            "name": self.name,
            "state": self.state,
            "currency": self.currency_id.name if self.currency_id else "IDR",
            "amount_untaxed": self.amount_untaxed,
            "amount_tax": self.amount_tax,
            "amount_total": self.amount_total,
            "carrier_id": self.carrier_id.id or None,
            "carrier_name": self.carrier_id.name or None,
            "is_pickup": self.custom_is_pickup,
            "pickup_store_id": self.warehouse_id.id if self.custom_is_pickup else None,
            "pickup_store_name": self.warehouse_id.name if self.custom_is_pickup else None,
            "shipping_address": (
                {
                    "name": self.partner_shipping_id.name,
                    "street": self.partner_shipping_id.street or "",
                    "city": self.partner_shipping_id.city or "",
                    "zip": self.partner_shipping_id.zip or "",
                    "phone": self.partner_shipping_id.phone or "",
                }
                if self.partner_shipping_id
                and self.partner_shipping_id.street
                and self.partner_shipping_id != self.partner_id
                else None
            ),
            "awb_number": self.x_awb_number or None,
            "awb_tracking_url": self.x_awb_tracking_url or None,
            "lines": lines,
        }
