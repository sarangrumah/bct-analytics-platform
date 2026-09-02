# -*- coding: utf-8 -*-
"""Gating toggle for the new consolidation chart engine."""

from __future__ import annotations

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    intercompany_consolidation_active = fields.Boolean(
        string="Enable Intercompany Consolidation",
        config_parameter="custom_accounting_full.intercompany_active",
        help="When enabled, exposes the multi-chart consolidation menu "
        "(custom.consolidation.chart / mappings / elimination rules).",
    )
