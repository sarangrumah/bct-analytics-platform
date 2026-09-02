# -*- coding: utf-8 -*-
import base64
import binascii

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class RentalCustomerPortal(CustomerPortal):
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "rental_count" in counters:
            partner = request.env.user.partner_id
            values["rental_count"] = request.env["rental.order"].search_count(
                [
                    ("partner_id", "=", partner.id),
                ]
            )
        return values

    def _rental_order_get(self, order_id):
        order = request.env["rental.order"].browse(order_id).exists()
        if not order:
            raise MissingError("Rental order not found")
        order.check_access("read")
        return order

    @http.route(["/my/rentals", "/my/rentals/page/<int:page>"], type="http", auth="user", website=True)
    def portal_my_rentals(self, page=1, **kw):
        partner = request.env.user.partner_id
        Order = request.env["rental.order"]
        domain = [("partner_id", "=", partner.id)]
        total = Order.search_count(domain)
        pager = portal_pager(
            url="/my/rentals",
            total=total,
            page=page,
            step=20,
        )
        rentals = Order.search(domain, order="pickup_dt desc", limit=20, offset=pager["offset"])
        return request.render(
            "custom_rental.portal_my_rentals",
            {
                "rentals": rentals,
                "page_name": "rental",
                "pager": pager,
                "default_url": "/my/rentals",
            },
        )

    @http.route(["/my/rentals/<int:order_id>"], type="http", auth="user", website=True)
    def portal_my_rental_detail(self, order_id, report_type=None, **kw):
        try:
            order = self._rental_order_get(order_id)
        except (AccessError, MissingError):
            return request.redirect("/my")
        if report_type == "pdf":
            pdf, _ct = (
                request.env["ir.actions.report"]
                .sudo()
                ._render_qweb_pdf("custom_rental.action_report_rental_contract", [order.id])
            )
            return request.make_response(
                pdf,
                headers=[
                    ("Content-Type", "application/pdf"),
                    ("Content-Disposition", 'attachment; filename="%s.pdf"' % order.name),
                ],
            )
        return request.render(
            "custom_rental.portal_my_rental_detail",
            {
                "order": order,
                "rental_order": order,
                "page_name": "rental",
            },
        )

    @http.route(["/my/rentals/<int:order_id>/sign"], type="jsonrpc", auth="user", website=True)
    def portal_rental_sign(self, order_id, signature=None, signed_by=None, **kw):
        try:
            order = self._rental_order_get(order_id)
        except (AccessError, MissingError):
            return {"error": "access"}
        if not signature:
            return {"error": "empty"}
        _, _, b64 = signature.partition("base64,")
        try:
            base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            return {"error": "invalid"}
        order.sudo().write(
            {
                "customer_signature": b64,
                "customer_signed_at": fields.Datetime.now(),
                "customer_signed_by": signed_by or request.env.user.name,
            }
        )
        return {"ok": True}
