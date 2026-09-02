# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobProviderSkuMap(models.Model):
    _name = "custom.ppob.provider.sku.map"
    _description = "Map internal PPOB product to provider SKU + buy price"
    _order = "provider_id, priority, product_id"

    provider_id = fields.Many2one("custom.ppob.provider", required=True, ondelete="cascade")
    product_id = fields.Many2one("custom.ppob.product", required=True, ondelete="restrict")
    provider_sku = fields.Char(required=True, help="Provider-side SKU / product code.")
    buy_price = fields.Monetary(required=True, currency_field="currency_id")
    priority = fields.Integer(default=100, help="Lower = preferred for failover routing.")
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    _prov_sku_uniq = models.Constraint(
        "unique(provider_id, provider_sku)",
        "Provider SKU must be unique within a provider.",
    )
