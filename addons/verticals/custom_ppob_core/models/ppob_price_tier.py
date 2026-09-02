# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PpobPriceTier(models.Model):
    _name = "custom.ppob.price.tier"
    _description = "PPOB Pricing Tier for Mitra"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="custom.ppob.price.tier.line",
        inverse_name="tier_id",
        string="Prices",
    )

    def _get_sell_price(self, partner, product):
        """Resolve the selling price for a mitra / product combination.

        Falls back to the product denomination when no tier line exists.
        """
        tier = partner.x_custom_ppob_mitra_tier_id or self
        if not tier:
            raise UserError("Partner %s has no price tier and no default is configured." % partner.display_name)
        line = self.env["custom.ppob.price.tier.line"].search(
            [
                ("tier_id", "=", tier.id),
                ("product_id", "=", product.id),
            ],
            limit=1,
        )
        if line:
            return line.sell_price
        if product.denom:
            return product.denom
        raise UserError(
            "No selling price for product %s in tier %s, and the product has "
            "no denomination to fall back on." % (product.display_name, tier.display_name)
        )


class PpobPriceTierLine(models.Model):
    _name = "custom.ppob.price.tier.line"
    _description = "PPOB Price Tier Line"
    _order = "tier_id, product_id"

    tier_id = fields.Many2one(
        comodel_name="custom.ppob.price.tier",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        comodel_name="custom.ppob.product",
        required=True,
        ondelete="restrict",
    )
    sell_price = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        related="tier_id.currency_id",
        store=True,
        readonly=True,
    )

    _tier_product_uniq = models.Constraint(
        "unique(tier_id, product_id)",
        "Each product can appear only once per tier.",
    )

    @api.constrains("sell_price")
    def _check_sell_price_positive(self):
        for line in self:
            if line.sell_price <= 0:
                raise ValidationError("Selling price must be positive.")
