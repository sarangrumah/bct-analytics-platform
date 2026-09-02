# -*- coding: utf-8 -*-
"""Public (anonymous) storefront API: catalog browse + shipping quote.

All routes ``auth='public'`` + CORS. No secrets, no mutation.
"""

from __future__ import annotations

import logging

from odoo import http
from odoo.http import request

from .cors import err, json_body, ok

_logger = logging.getLogger(__name__)

_MAX_LIMIT = 60
_DEFAULT_LIMIT = 24

# A product reaches the storefront only when it is BOTH sellable (`sale_ok`)
# and Published (`is_published`, the standard website_sale visibility toggle).
# Honouring `is_published` lets merchandisers hide a product from the headless
# storefront straight from the product form, exactly like Odoo's own website.
_PUBLISHED_DOMAIN = [("sale_ok", "=", True), ("is_published", "=", True)]

# Storefront locale (?lang=id|en) → Odoo language code for translatable fields.
_LANG_MAP = {"id": "id_ID", "en": "en_US"}


def _odoo_lang(kw):
    raw = (kw.get("lang") or "").strip().lower()[:2]
    return _LANG_MAP.get(raw)


class StorefrontPublicController(http.Controller):
    def _model(self, name, kw):
        """sudo model in the requested storefront language (for translations)."""
        model = request.env[name].sudo()
        lang = _odoo_lang(kw)
        return model.with_context(lang=lang) if lang else model

    @http.route(
        "/storefront/api/categories",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def categories(self, **kw):
        cats = self._model("product.public.category", kw).search([], order="sequence, name")
        data = [
            {
                "id": c.id,
                "name": c.name,
                "slug": str(c.id),
                "parent_id": c.parent_id.id or None,
            }
            for c in cats
        ]
        return ok(data)

    @http.route(
        "/storefront/api/products",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def products(self, **kw):
        domain = list(_PUBLISHED_DOMAIN)
        category = kw.get("category")
        if category:
            try:
                domain.append(("public_categ_ids", "child_of", int(category)))
            except (TypeError, ValueError):
                return err("BAD_CATEGORY", "category must be an integer")
        tag = kw.get("tag")
        if tag:
            try:
                tag_ids = [int(x) for x in str(tag).split(",") if x.strip()]
            except (TypeError, ValueError):
                return err("BAD_TAG", "tag must be integer id(s), comma-separated")
            if tag_ids:
                domain.append(("product_tag_ids", "in", tag_ids))
        search = (kw.get("q") or "").strip()
        if search:
            domain.append(("name", "ilike", search))

        # Price range filters on the catalog price (list_price) — consistent with
        # the price_asc/price_desc sort which also orders by list_price.
        price_domain = []
        for key, op in (("price_min", ">="), ("price_max", "<=")):
            raw = kw.get(key)
            if raw not in (None, ""):
                try:
                    price_domain.append(("list_price", op, float(raw)))
                except (TypeError, ValueError):
                    return err("BAD_PRICE", "%s must be a number" % key)

        try:
            page = max(int(kw.get("page", 1)), 1)
            limit = min(max(int(kw.get("limit", _DEFAULT_LIMIT)), 1), _MAX_LIMIT)
        except (TypeError, ValueError):
            return err("BAD_PAGINATION", "page/limit must be integers")

        sort_map = {
            "price_asc": "list_price asc",
            "price_desc": "list_price desc",
            "name": "name asc",
            "newest": "create_date desc",
        }
        order = sort_map.get(kw.get("sort"), "create_date desc")

        Product = self._model("product.template", kw)
        pricelist = Product._storefront_pricelist()
        # Price bounds reflect the category/tag/search selection (NOT the price
        # filter itself), so the slider range stays stable as the user drags it.
        cheapest = Product.search(domain, order="list_price asc", limit=1)
        dearest = Product.search(domain, order="list_price desc", limit=1)
        price_bounds = {
            "min": cheapest.list_price if cheapest else 0,
            "max": dearest.list_price if dearest else 0,
        }

        full_domain = domain + price_domain
        total = Product.search_count(full_domain)
        records = Product.search(full_domain, limit=limit, offset=(page - 1) * limit, order=order)
        items = [p._storefront_serialize(detail=False, pricelist=pricelist) for p in records]
        pages = (total + limit - 1) // limit if limit else 1
        return ok(
            {
                "items": items,
                "total": total,
                "page": page,
                "pages": pages,
                "price_bounds": price_bounds,
            }
        )

    @http.route(
        "/storefront/api/tags",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def tags(self, **kw):
        """Tags actually used on published products — drives PLP facet filtering."""
        products = self._model("product.template", kw).search(_PUBLISHED_DOMAIN)
        used = products.product_tag_ids if "product_tag_ids" in products._fields else products.browse()
        data = [{"id": t.id, "name": t.name} for t in used.sorted("name")]
        return ok(data)

    @http.route(
        "/storefront/api/products/<int:product_id>",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def product_detail(self, product_id, **kw):
        product = self._model("product.template", kw).browse(product_id)
        if not product.exists() or not product.sale_ok or not product.is_published:
            return err("NOT_FOUND", "Product not found", status=404)
        pricelist = product._storefront_pricelist()
        return ok(product._storefront_serialize(detail=True, pricelist=pricelist))

    @http.route(
        "/storefront/api/products/<int:product_id>/availability",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def product_availability(self, product_id, **kw):
        """In-store stock per published store (spec F11)."""
        product = request.env["product.template"].sudo().browse(product_id)
        if not product.exists() or not product.sale_ok or not product.is_published:
            return err("NOT_FOUND", "Product not found", status=404)
        return ok(product._storefront_store_availability())

    @http.route(
        "/storefront/api/content",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def content(self, **kw):
        """Editorial site content blocks (hero, sections, …) keyed by code."""
        blocks = self._model("custom.storefront.content", kw).search([])
        return ok({b.code: b._storefront_serialize() for b in blocks})

    @http.route(
        "/storefront/api/newsletter",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def newsletter(self, **kw):
        """Newsletter signup (explicit marketing consent)."""
        from odoo.exceptions import ValidationError

        email = (json_body().get("email") or "").strip()
        try:
            request.env["custom.storefront.subscriber"].sudo()._storefront_subscribe(email)
        except ValidationError as e:
            return err("INVALID_EMAIL", str(e))
        return ok({"subscribed": True})

    @http.route(
        "/storefront/api/stores",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def stores(self, **kw):
        """Physical store locator, sourced from published ``stock.warehouse``."""
        warehouses = (
            request.env["stock.warehouse"].sudo().search([("custom_storefront_published", "=", True)], order="name")
        )
        return ok([w._storefront_serialize() for w in warehouses])

    @http.route(
        "/storefront/api/shipping/quote",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def shipping_quote(self, **kw):
        body = json_body()
        items = body.get("items") or []
        if not items:
            return err("EMPTY_CART", "items required")
        # Build a transient draft order so id_rate_shipment has lines/weight.
        Order = request.env["sale.order"].sudo()
        website = Order._storefront_website()
        partner = website.user_id.partner_id if website and website.user_id else request.env.ref("base.public_partner")
        order = Order.new({"partner_id": partner.id, "website_id": website.id if website else False})
        order_lines = []
        for it in items:
            try:
                pid, qty = int(it["product_id"]), float(it.get("qty", 1))
            except (KeyError, TypeError, ValueError):
                return err("BAD_ITEM", "each item needs product_id and qty")
            order_lines.append((0, 0, {"product_id": pid, "product_uom_qty": qty}))
        order.order_line = order_lines
        if body.get("partner_zip"):
            order.partner_shipping_id = partner  # zip heuristic uses partner_shipping_id.zip
        quotes = Order._storefront_shipping_quotes(order)
        return ok(quotes)

    # ---------------- Payment methods ----------------

    @http.route(
        "/storefront/api/payment/methods",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def payment_methods(self, **kw):
        """List the payment providers published to the storefront.

        Visibility is driven by the stock ``payment.provider.is_published``
        flag plus the per-tenant master switch
        ``custom_storefront_api.payments_enabled``. The default provider code
        comes from ``custom_storefront_api.default_provider`` (falling back to
        the first published provider). Manual bank transfer is the native
        ``payment_custom`` wire-transfer provider (code ``custom``); its bank
        instructions live in ``pending_msg``.
        """
        icp = request.env["ir.config_parameter"].sudo()
        if str(icp.get_param("custom_storefront_api.payments_enabled", "True")).strip().lower() in (
            "false",
            "0",
            "",
        ):
            return ok({"default": "", "methods": []})

        lang = _odoo_lang(kw)
        Provider = request.env["payment.provider"].sudo()
        if lang:
            Provider = Provider.with_context(lang=lang)
        providers = Provider.search(
            [("state", "in", ("enabled", "test")), ("is_published", "=", True)],
            order="sequence, id",
        )
        methods = []
        for p in providers:
            is_manual = p.code == "custom"
            methods.append(
                {
                    "id": p.id,
                    "code": p.code,
                    "custom_mode": (p.custom_mode if "custom_mode" in p._fields else None) or None,
                    "label": p.name,
                    "type": "manual" if is_manual else "redirect",
                    "logo": f"/web/image/payment.provider/{p.id}/image_128" if p.image_128 else None,
                    "instructions": (p.pending_msg or "") if is_manual else "",
                    "sandbox": p.state == "test",
                }
            )
        default = icp.get_param("custom_storefront_api.default_provider", "") or (methods[0]["code"] if methods else "")
        return ok({"default": default, "methods": methods})
