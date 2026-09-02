# -*- coding: utf-8 -*-
"""Customer-facing storefront API.

- register/login/refresh: ``auth='public'`` (no token yet), issue JWT access
  + opaque refresh tokens.
- everything else: ``auth='jwt_storefront'`` — ``auth_jwt`` validates the
  Bearer token and sets ``request.jwt_partner_id``. We scope every query to
  that partner and never trust a partner id from the request body.
"""

from __future__ import annotations

import base64
import binascii
import logging

from odoo import http
from odoo.exceptions import AccessDenied, UserError, ValidationError
from odoo.http import request

from .cors import client_meta, err, json_body, ok

_logger = logging.getLogger(__name__)


def _validator():
    return request.env["auth.jwt.validator"].sudo()._get_validator_by_name("storefront")


def _access_ttl() -> int:
    return int(request.env["ir.config_parameter"].sudo().get_param("custom_storefront_api.access_ttl", "900"))


def _serialize_customer(partner) -> dict:
    return {
        "id": partner.id,
        "name": partner.name,
        "email": partner.email,
        "phone": partner.phone or None,
    }


def _issue_session(partner, user) -> dict:
    validator = _validator()
    ttl = _access_ttl()
    access = validator._encode(
        payload={"email": partner.email, "sub": str(partner.id), "name": partner.name},
        secret=validator.secret_key,
        expire=ttl,
    )
    ua, ip = client_meta()
    refresh = request.env["custom.storefront.token"].sudo()._issue(partner, user, ua, ip)
    return {
        "access": access,
        "refresh": refresh,
        "expires_in": ttl,
        "customer": _serialize_customer(partner),
    }


def _issue_guest_session(partner) -> dict:
    """Access-only session for guest checkout (no password, no refresh token)."""
    validator = _validator()
    ttl = _access_ttl()
    access = validator._encode(
        payload={"email": partner.email, "sub": str(partner.id), "name": partner.name},
        secret=validator.secret_key,
        expire=ttl,
    )
    return {
        "access": access,
        "refresh": "",
        "expires_in": ttl,
        "customer": _serialize_customer(partner),
        "is_guest": True,
    }


def _current_partner():
    """Resolve the authenticated customer partner from the JWT."""
    partner_id = getattr(request, "jwt_partner_id", None)
    if not partner_id:
        return request.env["res.partner"].browse()
    return request.env["res.partner"].sudo().browse(partner_id)


class StorefrontCustomerController(http.Controller):
    # ----------------- Auth -----------------

    @http.route(
        "/storefront/api/auth/register",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def register(self, **kw):
        body = json_body()
        try:
            partner = (
                request.env["res.partner"]
                .sudo()
                ._storefront_register(
                    email=body.get("email"),
                    password=body.get("password"),
                    name=body.get("name"),
                    phone=body.get("phone"),
                    consent_data=body.get("consent_data", False),
                    consent_marketing=body.get("consent_marketing", False),
                )
            )
        except ValidationError as e:
            return err("REGISTER_FAILED", str(e), status=400)
        return ok(_issue_session(partner, partner.user_ids[:1]))

    @http.route(
        "/storefront/api/auth/guest",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def guest(self, **kw):
        body = json_body()
        try:
            partner = (
                request.env["res.partner"]
                .sudo()
                ._storefront_guest(
                    email=body.get("email"),
                    name=body.get("name"),
                    consent_data=body.get("consent_data", False),
                )
            )
        except ValidationError as e:
            return err("GUEST_FAILED", str(e), status=400)
        return ok(_issue_guest_session(partner))

    @http.route(
        "/storefront/api/auth/login",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def login(self, **kw):
        body = json_body()
        try:
            partner = (
                request.env["res.partner"].sudo()._storefront_authenticate(body.get("email"), body.get("password"))
            )
        except AccessDenied:
            return err("INVALID_CREDENTIALS", "Invalid email or password", status=401)
        return ok(_issue_session(partner, partner.user_ids[:1]))

    @http.route(
        "/storefront/api/auth/refresh",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def refresh(self, **kw):
        body = json_body()
        Token = request.env["custom.storefront.token"].sudo()
        row = Token._resolve(body.get("refresh") or "")
        if not row:
            return err("INVALID_REFRESH", "Refresh token invalid or expired", status=401)
        ua, ip = client_meta()
        new_refresh = row._rotate(ua, ip)
        validator = _validator()
        ttl = _access_ttl()
        access = validator._encode(
            payload={"email": row.partner_id.email, "sub": str(row.partner_id.id), "name": row.partner_id.name},
            secret=validator.secret_key,
            expire=ttl,
        )
        return ok({"access": access, "refresh": new_refresh, "expires_in": ttl})

    @http.route(
        "/storefront/api/auth/logout",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def logout(self, **kw):
        body = json_body()
        row = request.env["custom.storefront.token"].sudo()._resolve(body.get("refresh") or "")
        if row:
            row._revoke()
        return ok({"logged_out": True})

    # ----------------- Profile / addresses -----------------

    @http.route(
        "/storefront/api/customer/me",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def me(self, **kw):
        partner = _current_partner()
        addresses = [
            {
                "id": a.id,
                "type": a.type,
                "name": a.name,
                "street": a.street,
                "city": a.city,
                "zip": a.zip,
                "phone": a.phone,
            }
            for a in partner.child_ids.filtered(lambda p: p.type in ("delivery", "invoice", "other"))
        ]
        return ok({"customer": _serialize_customer(partner), "addresses": addresses})

    @http.route(
        "/storefront/api/customer/addresses",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def address_create(self, **kw):
        partner = _current_partner()
        body = json_body()
        vals = {
            "parent_id": partner.id,
            "type": body.get("type", "delivery"),
            "name": body.get("name") or partner.name,
            "street": body.get("street"),
            "street2": body.get("street2"),
            "city": body.get("city"),
            "zip": body.get("zip"),
            "phone": body.get("phone"),
            "country_id": body.get("country_id") or partner.country_id.id,
        }
        addr = request.env["res.partner"].sudo().create(vals)
        return ok({"id": addr.id}, status=201)

    @http.route(
        "/storefront/api/customer/addresses/<int:address_id>",
        type="http",
        auth="jwt_storefront",
        methods=["PUT", "DELETE"],
        csrf=False,
        save_session=False,
    )
    def address_modify(self, address_id, **kw):
        partner = _current_partner()
        addr = request.env["res.partner"].sudo().browse(address_id)
        if not addr.exists() or addr.parent_id.id != partner.id:
            return err("NOT_FOUND", "Address not found", status=404)
        if request.httprequest.method == "DELETE":
            addr.unlink()
            return ok({"deleted": True})
        body = json_body()
        allowed = {"name", "street", "street2", "city", "zip", "phone", "type", "country_id"}
        addr.write({k: v for k, v in body.items() if k in allowed})
        return ok({"id": addr.id})

    # ----------------- Cart -----------------

    def _cart(self, partner):
        return request.env["sale.order"].sudo()._storefront_get_or_create_cart(partner)

    @http.route(
        "/storefront/api/cart",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def cart_get(self, **kw):
        return ok(self._cart(_current_partner())._storefront_serialize())

    @http.route(
        "/storefront/api/cart/items",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def cart_add(self, **kw):
        body = json_body()
        cart = self._cart(_current_partner())
        try:
            cart._storefront_add_line(body.get("product_id"), float(body.get("qty", 1)))
        except (ValidationError, UserError) as e:
            return err("CART_ADD_FAILED", str(e))
        return ok(cart._storefront_serialize())

    @http.route(
        "/storefront/api/cart/items/<int:line_id>",
        type="http",
        auth="jwt_storefront",
        methods=["PUT", "DELETE"],
        csrf=False,
        save_session=False,
    )
    def cart_line(self, line_id, **kw):
        cart = self._cart(_current_partner())
        try:
            if request.httprequest.method == "DELETE":
                cart._storefront_remove_line(line_id)
            else:
                cart._storefront_set_line_qty(line_id, float(json_body().get("qty", 1)))
        except (ValidationError, UserError) as e:
            return err("CART_UPDATE_FAILED", str(e))
        return ok(cart._storefront_serialize())

    @http.route(
        "/storefront/api/cart/shipping",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def cart_shipping(self, **kw):
        cart = self._cart(_current_partner())
        try:
            cart._storefront_apply_shipping(json_body().get("carrier_id"))
        except (ValidationError, UserError) as e:
            return err("SHIPPING_FAILED", str(e))
        return ok(cart._storefront_serialize())

    @http.route(
        "/storefront/api/cart/address",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def cart_address(self, **kw):
        """Set the cart's home-delivery address from inline fields (guest-friendly)."""
        cart = self._cart(_current_partner())
        try:
            cart._storefront_set_shipping_address(json_body())
        except (ValidationError, UserError) as e:
            return err("ADDRESS_FAILED", str(e))
        return ok(cart._storefront_serialize())

    @http.route(
        "/storefront/api/cart/pickup",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def cart_pickup(self, **kw):
        """Switch the cart to in-store pickup (click & collect) at a store."""
        cart = self._cart(_current_partner())
        try:
            cart._storefront_set_pickup(json_body().get("warehouse_id"))
        except (ValidationError, UserError) as e:
            return err("PICKUP_FAILED", str(e))
        return ok(cart._storefront_serialize())

    # ----------------- Wishlist -----------------

    def _wishlist_payload(self, partner):
        pricelist = request.env["product.template"].sudo()._storefront_pricelist()
        entries = request.env["custom.wishlist"].sudo().search([("partner_id", "=", partner.id)])
        return [e._storefront_serialize(pricelist=pricelist) for e in entries]

    @http.route(
        "/storefront/api/wishlist",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def wishlist_get(self, **kw):
        return ok(self._wishlist_payload(_current_partner()))

    @http.route(
        "/storefront/api/wishlist",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def wishlist_add(self, **kw):
        partner = _current_partner()
        body = json_body()
        product_id = body.get("product_id")
        if not product_id:
            return err("BAD_ITEM", "product_id is required")
        entry = request.env["custom.wishlist"].sudo()._storefront_add(partner, product_id, body.get("variant_id"))
        if not entry:
            return err("NOT_FOUND", "Product not found", status=404)
        return ok(self._wishlist_payload(partner))

    @http.route(
        "/storefront/api/wishlist/<int:product_tmpl_id>",
        type="http",
        auth="jwt_storefront",
        methods=["DELETE"],
        csrf=False,
        save_session=False,
    )
    def wishlist_remove(self, product_tmpl_id, **kw):
        partner = _current_partner()
        request.env["custom.wishlist"].sudo().search(
            [("partner_id", "=", partner.id), ("product_tmpl_id", "=", product_tmpl_id)]
        ).unlink()
        return ok(self._wishlist_payload(partner))

    @http.route(
        "/storefront/api/wishlist/<int:product_tmpl_id>/move-to-cart",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def wishlist_move_to_cart(self, product_tmpl_id, **kw):
        """Add the product to the cart and drop it from the wishlist atomically."""
        partner = _current_partner()
        qty = float(json_body().get("qty", 1) or 1)
        cart = self._cart(partner)
        try:
            cart._storefront_add_line(product_tmpl_id, qty)
        except (ValidationError, UserError) as e:
            return err("CART_ADD_FAILED", str(e))
        request.env["custom.wishlist"].sudo().search(
            [("partner_id", "=", partner.id), ("product_tmpl_id", "=", product_tmpl_id)]
        ).unlink()
        return ok(
            {
                "cart": cart._storefront_serialize(),
                "wishlist": self._wishlist_payload(partner),
            }
        )

    @http.route(
        "/storefront/api/wishlist/move-all-to-cart",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def wishlist_move_all_to_cart(self, **kw):
        """Add every in-stock wishlist item to the cart and drop those rows.

        Out-of-stock items are left in the wishlist. Per-item failures are
        skipped so one bad line never aborts the whole move.
        """
        partner = _current_partner()
        cart = self._cart(partner)
        entries = request.env["custom.wishlist"].sudo().search([("partner_id", "=", partner.id)])
        moved = entries.browse()
        for entry in entries:
            if not entry.product_tmpl_id._storefront_in_stock():
                continue
            try:
                cart._storefront_add_line(entry.product_tmpl_id.id, 1.0)
            except (ValidationError, UserError):
                continue
            moved |= entry
        moved.unlink()
        return ok(
            {
                "cart": cart._storefront_serialize(),
                "wishlist": self._wishlist_payload(partner),
                "moved": len(moved),
            }
        )

    # ----------------- Affiliate self-serve (spec F6) -----------------

    def _affiliate_for(self, partner):
        if "custom.affiliate" not in request.env:
            return None
        return request.env["custom.affiliate"].sudo().search([("partner_id", "=", partner.id)], limit=1)

    @http.route(
        "/storefront/api/affiliate/me",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def affiliate_me(self, **kw):
        if "custom.affiliate" not in request.env:
            return err("NOT_AVAILABLE", "Affiliate program not enabled", status=404)
        aff = self._affiliate_for(_current_partner())
        if not aff:
            return ok({"is_affiliate": False})
        return ok(aff._storefront_dashboard())

    @http.route(
        "/storefront/api/affiliate/apply",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def affiliate_apply(self, **kw):
        if "custom.affiliate" not in request.env:
            return err("NOT_AVAILABLE", "Affiliate program not enabled", status=404)
        partner = _current_partner()
        aff = self._affiliate_for(partner)
        if not aff:
            aff = (
                request.env["custom.affiliate"]
                .sudo()
                .create(
                    {
                        "partner_id": partner.id,
                        "state": "active",  # self-serve auto-activate; admin can suspend
                    }
                )
            )
        return ok(aff._storefront_dashboard())

    @http.route(
        "/storefront/api/affiliate/links",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def affiliate_create_link(self, **kw):
        aff = self._affiliate_for(_current_partner())
        if not aff:
            return err("NOT_AFFILIATE", "You are not an affiliate", status=403)
        body = json_body()
        target = (body.get("target_url") or "/").strip() or "/"
        if not target.startswith("/"):
            target = "/" + target
        request.env["custom.affiliate.link"].sudo().create(
            {
                "affiliate_id": aff.id,
                "name": (body.get("name") or "Link").strip()[:64],
                "target_url": target[:200],
            }
        )
        return ok(aff._storefront_dashboard())

    # ----------------- Checkout / orders -----------------

    @http.route(
        "/storefront/api/checkout",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def checkout(self, **kw):
        body = json_body()
        cart = self._cart(_current_partner())
        # Affiliate attribution (custom_affiliate): tag the order with the
        # visitor's affiliate code (from the storefront cookie) before confirm.
        aff_code = body.get("affiliate_code")
        if aff_code and "custom_affiliate_code" in cart._fields:
            cart.sudo().write({"custom_affiliate_code": str(aff_code)[:64]})
        try:
            cart._storefront_checkout(
                shipping_address_id=body.get("shipping_address_id"),
                billing_address_id=body.get("billing_address_id"),
                carrier_id=body.get("carrier_id"),
                pickup_warehouse_id=body.get("pickup_warehouse_id"),
                shipping_address=body.get("shipping_address"),
            )
        except (ValidationError, UserError) as e:
            return err("CHECKOUT_FAILED", str(e))
        return ok({"order_id": cart.id, "name": cart.name, "amount_total": cart.amount_total})

    @http.route(
        "/storefront/api/checkout/<int:order_id>/pay",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def pay(self, order_id, **kw):
        partner = _current_partner()
        body = json_body()
        icp = request.env["ir.config_parameter"].sudo()
        order = request.env["sale.order"].sudo().browse(order_id)
        if not order.exists() or order.partner_id.id != partner.id:
            return err("NOT_FOUND", "Order not found", status=404)

        # Only providers published to the storefront are payable here. The
        # requested code (or the configured default) must resolve to one.
        domain = [("state", "in", ("enabled", "test")), ("is_published", "=", True)]
        provider_code = (body.get("provider_code") or "").strip() or icp.get_param(
            "custom_storefront_api.default_provider", ""
        )
        provider = request.env["payment.provider"]
        if provider_code:
            provider = request.env["payment.provider"].sudo().search(domain + [("code", "=", provider_code)], limit=1)
        if not provider:
            # Fall back to the first published provider (sequence order).
            provider = request.env["payment.provider"].sudo().search(domain, order="sequence, id", limit=1)
        if not provider:
            return err("NO_PROVIDER", f"No published provider for '{provider_code or 'default'}'")

        payment_method = provider.payment_method_ids[:1]
        if not payment_method:
            return err(
                "NO_PAYMENT_METHOD",
                f"Provider '{provider.code}' has no enabled payment method.",
            )
        Tx = request.env["payment.transaction"].sudo()
        tx = Tx.create(
            {
                "provider_id": provider.id,
                "payment_method_id": payment_method.id,
                "reference": Tx._compute_reference(provider.code, prefix=order.name),
                "amount": order.amount_total,
                "currency_id": order.currency_id.id,
                "partner_id": partner.id,
                "sale_order_ids": [(6, 0, [order.id])],
            }
        )
        # Manual bank transfer (native payment_custom wire transfer): no
        # outbound API call. Move the transaction to pending and hand the
        # customer the bank instructions; an admin verifies the proof later.
        if provider.code == "custom":
            try:
                tx._set_pending()
            except (UserError, ValidationError) as e:
                return err("PAYMENT_INIT_FAILED", str(e))
            return ok(
                {
                    "type": "manual",
                    "reference": tx.reference,
                    "state": tx.state,
                    "provider": provider.name,
                    "instructions": provider.pending_msg or "",
                }
            )
        # Redirect gateways (Midtrans/Xendit/DOKU/POS) via adapters.
        try:
            tx._send_payment_request()
        except (UserError, ValidationError) as e:
            return err("PAYMENT_INIT_FAILED", str(e))
        return ok(
            {
                "type": "redirect",
                "redirect_url": tx.x_id_redirect_url,
                "reference": tx.reference,
                "state": tx.state,
            }
        )

    @http.route(
        "/storefront/api/orders",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def orders(self, **kw):
        partner = _current_partner()
        records = (
            request.env["sale.order"]
            .sudo()
            .search([("partner_id", "=", partner.id), ("state", "!=", "draft")], order="id desc", limit=100)
        )
        data = [
            {
                "order_id": o.id,
                "name": o.name,
                "state": o.state,
                "amount_total": o.amount_total,
                "currency": o.currency_id.name,
                "date": o.date_order and o.date_order.isoformat(),
                "awb_number": o.x_awb_number or None,
            }
            for o in records
        ]
        return ok(data)

    @http.route(
        "/storefront/api/orders/<int:order_id>",
        type="http",
        auth="jwt_storefront",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def order_detail(self, order_id, **kw):
        partner = _current_partner()
        order = request.env["sale.order"].sudo().browse(order_id)
        if not order.exists() or order.partner_id.id != partner.id:
            return err("NOT_FOUND", "Order not found", status=404)
        data = order._storefront_serialize()
        tx = (
            request.env["payment.transaction"]
            .sudo()
            .search([("sale_order_ids", "in", order.id)], order="id desc", limit=1)
        )
        data["payment"] = {"state": tx.state, "reference": tx.reference} if tx else None
        return ok(data)

    @http.route(
        "/storefront/api/checkout/<int:order_id>/payment-proof",
        type="http",
        auth="jwt_storefront",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def payment_proof(self, order_id, **kw):
        """Submit a manual bank-transfer proof for an order (wire transfer).

        Captured against the order's latest pending ``custom`` (wire transfer)
        transaction; a payments manager verifies it in the backend, which
        settles the transaction.
        """
        partner = _current_partner()
        body = json_body()
        order = request.env["sale.order"].sudo().browse(order_id)
        if not order.exists() or order.partner_id.id != partner.id:
            return err("NOT_FOUND", "Order not found", status=404)

        tx = (
            request.env["payment.transaction"]
            .sudo()
            .search(
                [("sale_order_ids", "in", order.id), ("provider_code", "=", "custom")],
                order="id desc",
                limit=1,
            )
        )

        image_b64 = (body.get("image") or "").strip()
        if image_b64.lower().startswith("data:") and "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        image_val = False
        if image_b64:
            try:
                base64.b64decode(image_b64, validate=True)
            except (binascii.Error, ValueError):
                return err("BAD_IMAGE", "image must be valid base64")
            image_val = image_b64

        try:
            amount = float(body.get("amount") or order.amount_total)
        except (TypeError, ValueError):
            amount = order.amount_total

        proof = (
            request.env["custom.storefront.payment.proof"]
            .sudo()
            .create(
                {
                    "transaction_id": tx.id if tx else False,
                    "sale_order_id": order.id,
                    "partner_id": partner.id,
                    "currency_id": order.currency_id.id,
                    "amount": amount,
                    "bank_reference": (body.get("bank_reference") or "")[:128],
                    "sender_name": (body.get("sender_name") or "")[:128],
                    "paid_date": body.get("paid_date") or False,
                    "note": (body.get("note") or "")[:1000],
                    "proof_image": image_val,
                    "proof_filename": (body.get("filename") or "")[:128],
                }
            )
        )
        order.message_post(
            body=(
                f"Customer submitted manual transfer proof {proof.name} "
                f"(ref: {proof.bank_reference or '-'}, amount: {proof.amount})."
            )
        )
        return ok({"proof_id": proof.id, "state": proof.state, "reference": proof.name})
