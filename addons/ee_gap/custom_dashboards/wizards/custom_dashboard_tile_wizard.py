# -*- coding: utf-8 -*-
"""Wizard to create a tile interactively with model/field pickers and preview."""

from __future__ import annotations


from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomDashboardTileWizard(models.TransientModel):
    _name = "custom.dashboard.tile.wizard"
    _description = "Custom Dashboard Tile Wizard"

    dashboard_id = fields.Many2one(
        "custom.dashboard",
        string="Dashboard",
        required=True,
    )
    name = fields.Char(required=True)
    tile_type = fields.Selection(
        [
            ("count", "Count"),
            ("sum", "Sum"),
            ("avg", "Average"),
            ("last_value", "Last Value"),
            ("formula", "Formula"),
            ("chart_bar", "Bar Chart"),
            ("chart_pie", "Pie Chart"),
        ],
        default="count",
        required=True,
    )
    ir_model_id = fields.Many2one(
        "ir.model",
        string="Source Model",
        domain="[('transient', '=', False)]",
    )
    model_name = fields.Char(related="ir_model_id.model", store=False, readonly=True)
    measure_field_id = fields.Many2one(
        "ir.model.fields",
        string="Measure Field",
        domain="[('model_id', '=', ir_model_id), ('ttype', 'in', ['integer','float','monetary'])]",
    )
    groupby_field_id = fields.Many2one(
        "ir.model.fields",
        string="Group By Field",
        domain="[('model_id', '=', ir_model_id)]",
    )
    domain = fields.Char(default="[]")
    formula_expression = fields.Text()
    refresh_interval_seconds = fields.Integer(default=300)
    color = fields.Char(default="#1f77b4")
    preview_result = fields.Text(readonly=True)

    @api.onchange("ir_model_id")
    def _onchange_ir_model_id(self):
        self.measure_field_id = False
        self.groupby_field_id = False

    def action_preview(self):
        """Build a transient tile, refresh it, and display the result."""
        self.ensure_one()
        Tile = self.env["custom.dashboard.tile"].new(
            {
                "dashboard_id": self.dashboard_id.id,
                "name": self.name or "Preview",
                "tile_type": self.tile_type,
                "model_name": self.model_name,
                "domain": self.domain or "[]",
                "measure_field": self.measure_field_id.name or False,
                "groupby_field": self.groupby_field_id.name or False,
                "formula_expression": self.formula_expression or False,
                "refresh_interval_seconds": self.refresh_interval_seconds or 300,
            }
        )
        # NewId records can't call write/search reliably; create a real one,
        # refresh, then unlink to avoid polluting the DB.
        tile = self.env["custom.dashboard.tile"].create(
            {
                "dashboard_id": self.dashboard_id.id,
                "name": (self.name or "Preview") + " (preview)",
                "tile_type": self.tile_type,
                "model_name": self.model_name,
                "domain": self.domain or "[]",
                "measure_field": self.measure_field_id.name or False,
                "groupby_field": self.groupby_field_id.name or False,
                "formula_expression": self.formula_expression or False,
                "refresh_interval_seconds": self.refresh_interval_seconds or 300,
            }
        )
        try:
            tile.action_refresh()
            self.preview_result = tile.cached_value or ""
        finally:
            tile.unlink()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create_tile(self):
        self.ensure_one()
        if not self.dashboard_id:
            raise UserError(_("Choose a dashboard first."))
        tile = self.env["custom.dashboard.tile"].create(
            {
                "dashboard_id": self.dashboard_id.id,
                "name": self.name,
                "tile_type": self.tile_type,
                "model_name": self.model_name,
                "domain": self.domain or "[]",
                "measure_field": self.measure_field_id.name or False,
                "groupby_field": self.groupby_field_id.name or False,
                "formula_expression": self.formula_expression or False,
                "refresh_interval_seconds": self.refresh_interval_seconds or 300,
                "color": self.color or "#1f77b4",
            }
        )
        tile.action_refresh()
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.dashboard",
            "res_id": self.dashboard_id.id,
            "view_mode": "form",
            "target": "current",
        }
