# -*- coding: utf-8 -*-
"""Settings for super-admin: orchestrator URL + Grafana embed URL."""

from __future__ import annotations

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    custom_super_admin_orchestrator_url = fields.Char(
        config_parameter="custom_super_admin.orchestrator_url",
        default="http://tenant-orchestrator:8080",
        string="Orchestrator URL",
        help="Base URL of the tenant-orchestrator service. Internal Docker DNS name in normal multitenant deploy.",
    )
    custom_super_admin_grafana_base_url = fields.Char(
        config_parameter="custom_super_admin.grafana_base_url",
        default="",
        string="Grafana Base URL",
        help="Public base URL where Grafana is reachable for ops dashboards (e.g. https://grafana.platform.localhost).",
    )
