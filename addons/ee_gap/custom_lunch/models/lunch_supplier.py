# -*- coding: utf-8 -*-
"""Lunch supplier extensions (Indonesia EE)."""

from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError


# Public deep-link templates. Mobile apps will intercept; otherwise the
# browser falls through to the public web listing on the same host.
_VENDOR_URL_TEMPLATES = {
    "gofood": "https://gofood.co.id/restaurant/{merchant_id}",
    "grabfood": "https://food.grab.com/id/id/restaurant/{merchant_id}",
    "shopeefood": "https://shopeefood.co.id/restaurant/{merchant_id}",
}

_VENDOR_LABELS = {
    "gofood": "GoFood",
    "grabfood": "GrabFood",
    "shopeefood": "ShopeeFood",
}


class LunchSupplier(models.Model):
    _inherit = "lunch.supplier"

    x_id_vendor_type = fields.Selection(
        [
            ("walking", "Walking"),
            ("delivery", "Delivery"),
            ("gofood", "GoFood"),
            ("grabfood", "GrabFood"),
            ("shopeefood", "ShopeeFood"),
            ("direct", "Direct"),
        ],
        string="Vendor Type (ID)",
        default="direct",
    )
    x_id_currency_id = fields.Many2one(
        "res.currency",
        string="Currency (ID)",
        default=lambda self: self.env.company.currency_id,
    )
    x_id_min_order = fields.Monetary(
        string="Minimum Order (ID)",
        currency_field="x_id_currency_id",
    )
    x_id_halal_certified = fields.Boolean(
        string="Halal Certified",
        default=False,
    )
    x_id_gofood_id = fields.Char(string="GoFood Merchant ID")
    x_id_grabfood_id = fields.Char(string="GrabFood Merchant ID")
    x_id_shopeefood_id = fields.Char(string="ShopeeFood Merchant ID")

    x_partner_app_url = fields.Char(
        string="Vendor App URL",
        compute="_compute_partner_app_url",
        store=True,
        help="Deep link to the vendor's listing page on the matching food-delivery app.",
    )

    @api.depends(
        "x_id_vendor_type",
        "x_id_gofood_id",
        "x_id_grabfood_id",
        "x_id_shopeefood_id",
    )
    def _compute_partner_app_url(self):
        for rec in self:
            vtype = rec.x_id_vendor_type
            merchant = False
            if vtype == "gofood":
                merchant = rec.x_id_gofood_id
            elif vtype == "grabfood":
                merchant = rec.x_id_grabfood_id
            elif vtype == "shopeefood":
                merchant = rec.x_id_shopeefood_id
            if vtype in _VENDOR_URL_TEMPLATES and merchant:
                rec.x_partner_app_url = _VENDOR_URL_TEMPLATES[vtype].format(
                    merchant_id=quote(merchant.strip(), safe="")
                )
            else:
                rec.x_partner_app_url = False

    def action_open_vendor_app(self):
        """Open the vendor's deep link in a new browser tab."""
        self.ensure_one()
        if not self.x_partner_app_url:
            label = _VENDOR_LABELS.get(self.x_id_vendor_type, _("vendor app"))
            raise UserError(
                _("No %(label)s merchant ID configured for supplier %(name)s.")
                % {
                    "label": label,
                    "name": self.name,
                }
            )
        return {
            "type": "ir.actions.act_url",
            "url": self.x_partner_app_url,
            "target": "new",
        }
