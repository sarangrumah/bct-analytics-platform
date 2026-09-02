# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    fixed_asset_ids = fields.One2many(
        comodel_name="custom.fixed.asset",
        compute="_compute_fixed_asset_ids",
        string="Fixed Assets",
    )
    fixed_asset_count = fields.Integer(
        compute="_compute_fixed_asset_ids",
    )

    @api.depends("order_line")
    def _compute_fixed_asset_ids(self):
        Asset = self.env["custom.fixed.asset"].sudo()
        for order in self:
            assets = Asset.search([("purchase_line_id", "in", order.order_line.ids)])
            order.fixed_asset_ids = assets
            order.fixed_asset_count = len(assets)

    def action_view_fixed_assets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Fixed Assets"),
            "res_model": "custom.fixed.asset",
            "view_mode": "list,form",
            "domain": [("purchase_line_id", "in", self.order_line.ids)],
        }
