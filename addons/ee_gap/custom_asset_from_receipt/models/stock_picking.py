# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    fixed_asset_ids = fields.One2many(
        comodel_name="custom.fixed.asset",
        inverse_name="picking_id",
        string="Fixed Assets",
    )
    fixed_asset_count = fields.Integer(
        compute="_compute_fixed_asset_count",
    )
    has_rental_asset_lines = fields.Boolean(
        compute="_compute_has_rental_asset_lines",
    )

    @api.depends("fixed_asset_ids")
    def _compute_fixed_asset_count(self):
        for picking in self:
            picking.fixed_asset_count = len(picking.fixed_asset_ids)

    @api.depends("move_line_ids.product_id.is_rental_asset", "state", "picking_type_id.code")
    def _compute_has_rental_asset_lines(self):
        for picking in self:
            picking.has_rental_asset_lines = (
                picking.state == "done"
                and picking.picking_type_id.code == "incoming"
                and any(ml.product_id.is_rental_asset for ml in picking.move_line_ids)
            )

    def action_open_asset_conversion_wizard(self):
        self.ensure_one()
        if self.state != "done":
            raise UserError(_("Receipt must be validated before converting to assets."))
        if self.picking_type_id.code != "incoming":
            raise UserError(_("Asset conversion is only available on incoming receipts."))
        wizard = self.env["custom.asset.conversion.wizard"].create(
            {
                "picking_id": self.id,
            }
        )
        wizard._populate_lines()
        return {
            "type": "ir.actions.act_window",
            "name": _("Convert to Fixed Assets"),
            "res_model": "custom.asset.conversion.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_fixed_assets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Fixed Assets"),
            "res_model": "custom.fixed.asset",
            "view_mode": "list,form",
            "domain": [("picking_id", "=", self.id)],
        }
