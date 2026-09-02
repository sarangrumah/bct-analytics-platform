# -*- coding: utf-8 -*-
"""Dynamic input fields for game products (Get Gamelist / Direct Top Up).

Each row declares a per-product input (userid / zoneid / server) so
``game-list`` can advertise the fields POS must collect and ``direct-topup`` can
validate the inbound ``field`` dict before dispatch.
"""

from odoo import fields, models


class PpsGameField(models.Model):
    _name = "custom.ppob.pps.game.field"
    _description = "PPS Gateway: Game Dynamic Field"
    _order = "product_id, sequence, id"

    product_id = fields.Many2one(
        comodel_name="custom.ppob.product",
        string="Product",
        required=True,
        ondelete="cascade",
        index=True,
    )
    key = fields.Char(
        required=True,
        help="Field key sent in the direct-topup 'field' object (e.g. userid, zoneid, server).",
    )
    label = fields.Char(required=True)
    field_type = fields.Selection(
        selection=[("string", "String"), ("number", "Number")],
        default="string",
        required=True,
    )
    required = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    _product_key_uniq = models.Constraint("unique(product_id, key)", "Duplicate field key for this product.")
