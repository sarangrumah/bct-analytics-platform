# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomFixedAsset(models.Model):
    _inherit = "custom.fixed.asset"

    lot_id = fields.Many2one(
        comodel_name="stock.lot",
        string="Serial/Lot",
        index=True,
        copy=False,
        help="Serial number (or lot) from the inventory receipt that this asset represents.",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Source Product",
        index=True,
    )
    purchase_line_id = fields.Many2one(
        comodel_name="purchase.order.line",
        string="Source Purchase Line",
        index=True,
    )
    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Source Receipt",
        index=True,
    )
    rental_asset_ids = fields.One2many(
        comodel_name="rental.asset",
        inverse_name="fixed_asset_id",
        string="Linked Rental Assets",
    )

    _lot_unique_per_asset = models.Constraint(
        "UNIQUE(lot_id)",
        "A serial/lot can only be converted into one fixed asset.",
    )
