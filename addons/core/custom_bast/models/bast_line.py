# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import fields, models


class CustomBastLine(models.Model):
    _name = "custom.bast.line"
    _description = "BAST Line"
    _order = "bast_id, sequence, id"

    bast_id = fields.Many2one(
        "custom.bast.document",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    item_description = fields.Char(required=True)
    qty = fields.Float(default=1.0, required=True)
    uom_id = fields.Many2one("uom.uom", string="UoM")
    product_id = fields.Many2one("product.product", string="Product")
    lot_id = fields.Many2one("stock.lot", string="Lot / Serial")
    condition = fields.Selection(
        [
            ("good", "Good"),
            ("damaged", "Damaged"),
            ("partial", "Partial"),
        ],
        default="good",
        required=True,
    )
    photo = fields.Binary(attachment=True)
    note = fields.Char()
