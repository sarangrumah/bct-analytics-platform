# -*- coding: utf-8 -*-
"""Extend ``custom.ops.tenant.health`` with a VPS reference."""

from __future__ import annotations

from odoo import fields, models


class TenantHealth(models.Model):
    _inherit = "custom.ops.tenant.health"

    vps_id = fields.Many2one(
        "tenant.vps",
        string="VPS",
        compute="_compute_vps",
        store=True,
        index=True,
    )

    def _compute_vps(self):
        for rec in self:
            rec.vps_id = rec.tenant_id.primary_vps_id.id if rec.tenant_id else False
