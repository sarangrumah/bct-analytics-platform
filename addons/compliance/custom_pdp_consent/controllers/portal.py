# -*- coding: utf-8 -*-
"""Portal endpoints to view & withdraw subject consents."""

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class PdpConsentPortal(CustomerPortal):
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "consent_count" in counters:
            partner = request.env.user.partner_id
            values["consent_count"] = (
                request.env["pdp.consent"]
                .sudo()
                .search_count(
                    [
                        ("partner_id", "=", partner.id),
                    ]
                )
            )
        return values

    @http.route(["/my/consents"], type="http", auth="user", website=True)
    def portal_my_consents(self, **kw):
        partner = request.env.user.partner_id
        consents = (
            request.env["pdp.consent"]
            .sudo()
            .search(
                [
                    ("partner_id", "=", partner.id),
                ],
                order="given_at desc",
            )
        )
        return request.render(
            "custom_pdp_consent.portal_my_consents",
            {
                "consents": consents,
                "page_name": "consents",
            },
        )

    @http.route(
        ["/my/consents/<int:consent_id>/withdraw"], type="http", auth="user", website=True, methods=["POST"], csrf=True
    )
    def portal_withdraw_consent(self, consent_id, **kw):
        partner = request.env.user.partner_id
        consent = request.env["pdp.consent"].sudo().browse(consent_id)
        if not consent.exists() or consent.partner_id.id != partner.id:
            return request.redirect("/my/consents")
        consent.action_withdraw(reason=kw.get("reason") or "Withdrawn via portal")
        return request.redirect("/my/consents")
