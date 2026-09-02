# -*- coding: utf-8 -*-
from __future__ import annotations

import secrets

from odoo import fields, models


class CustomAffiliateLink(models.Model):
    _name = "custom.affiliate.link"  # nosemgrep
    _description = "Affiliate Tracked Link"
    _order = "create_date desc"

    name = fields.Char(string="Label", required=True, default="Link")
    affiliate_id = fields.Many2one(
        "custom.affiliate", string="Affiliate", required=True, ondelete="cascade", index=True
    )
    target_url = fields.Char(
        string="Target Path",
        default="/",
        help="Storefront path the link points to (e.g. /products/12 or /).",
    )
    short_code = fields.Char(
        string="Short Code",
        required=True,
        copy=False,
        index=True,
        default=lambda self: secrets.token_urlsafe(6),
    )
    utm_source = fields.Char(default="affiliate")
    utm_medium = fields.Char(default="referral")
    utm_campaign = fields.Char()
    click_count = fields.Integer(compute="_compute_click_count")
    full_url = fields.Char(compute="_compute_full_url")

    _short_code_uniq = models.Constraint(
        "unique(short_code)",
        "The short code must be unique.",
    )

    def _compute_click_count(self):
        Click = self.env["custom.affiliate.click"]
        for link in self:
            link.click_count = Click.search_count([("link_id", "=", link.id)])

    def _storefront_serialize(self) -> dict:
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "target_url": self.target_url,
            "short_code": self.short_code,
            "full_url": self.full_url,
            "click_count": self.click_count,
        }

    def _compute_full_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("custom_affiliate.storefront_base_url", "").rstrip("/")
        for link in self:
            code = link.affiliate_id.affiliate_code or ""
            path = link.target_url or "/"
            sep = "&" if "?" in path else "?"
            link.full_url = "%s%s%saff=%s" % (base, path, sep, code) if base else ""
