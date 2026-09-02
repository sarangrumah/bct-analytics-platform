# -*- coding: utf-8 -*-
"""Extend ``tenant.registry`` with environment + primary-VPS relations."""

from __future__ import annotations

from odoo import api, fields, models


class TenantRegistry(models.Model):
    _inherit = "tenant.registry"

    environment_ids = fields.One2many(
        "tenant.environment",
        "tenant_registry_id",
        string="Environments",
    )
    primary_vps_id = fields.Many2one(
        "tenant.vps",
        compute="_compute_primary_vps",
        store=True,
        string="Primary (Prod) VPS",
    )

    @api.depends("environment_ids.env_type", "environment_ids.vps_id")
    def _compute_primary_vps(self):
        for rec in self:
            prod = rec.environment_ids.filtered(lambda e: e.env_type == "prod")
            rec.primary_vps_id = prod[:1].vps_id.id if prod else False
