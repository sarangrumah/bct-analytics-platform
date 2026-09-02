# -*- coding: utf-8 -*-
"""Handling Unit / pallet tracking for putaway volumetrics."""

from __future__ import annotations

from odoo import _, api, fields, models


class WmsHdPallet(models.Model):
    _name = "custom.wms.hd.pallet"
    _description = "WMS Handling Unit / Pallet"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda s: s.env._("New"))
    barcode = fields.Char(index=True, copy=False)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    location_id = fields.Many2one("stock.location", index=True)
    product_id = fields.Many2one("product.product")
    qty = fields.Float(default=0.0)
    volume_m3 = fields.Float(default=0.0)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_use", "In Use"),
            ("empty", "Empty"),
            ("scrapped", "Scrapped"),
        ],
        default="draft",
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                seq = self.env["ir.sequence"].next_by_code("custom.wms.hd.pallet")
                vals["name"] = seq or _("HU/NEW")
        return super().create(vals_list)
