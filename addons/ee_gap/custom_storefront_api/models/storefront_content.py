# -*- coding: utf-8 -*-
"""Editorial site content the storefront needs but Odoo's catalog can't supply.

Hero banner, section images, headlines/CTA copy — everything that is *not*
product/stock/price data (which stays in the catalog models). Kept in Odoo (no
separate CMS service) so it's edited in the same admin and reuses the storefront
API + BFF. Each row is a content *block* addressed by a stable ``code``.
"""

from __future__ import annotations

from odoo import fields, models


class StorefrontContent(models.Model):
    _name = "custom.storefront.content"  # nosemgrep
    _description = "Storefront Editorial Content Block"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True)
    code = fields.Char(
        string="Code",
        required=True,
        index=True,
        help="Stable key the storefront references, e.g. 'hero', 'editorial', 'featured'.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    eyebrow = fields.Char(translate=True)
    headline = fields.Char(translate=True)
    body = fields.Text(translate=True)
    cta_label = fields.Char(string="CTA Label", translate=True)
    cta_url = fields.Char(string="CTA URL", default="/products")
    image = fields.Binary(attachment=True)

    _code_uniq = models.Constraint(
        "unique(code)",
        "The content block code must be unique.",
    )

    def _storefront_serialize(self) -> dict:
        self.ensure_one()
        return {
            "code": self.code,
            "eyebrow": self.eyebrow or "",
            "headline": self.headline or "",
            "body": self.body or "",
            "cta_label": self.cta_label or "",
            "cta_url": self.cta_url or "",
            "image": (f"/web/image/custom.storefront.content/{self.id}/image" if self.image else None),
        }
