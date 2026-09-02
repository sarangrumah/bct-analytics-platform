# -*- coding: utf-8 -*-
"""``tenant.environment`` — per-tenant environment (dev/staging/prod).

A ``prod`` environment is 1:1 with a VPS (enforced via SQL constraint).
``dev``/``staging`` may share a VPS (typically the platform-shared one).
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TenantEnvironment(models.Model):
    _name = "tenant.environment"
    _description = "Tenant Environment"
    _order = "tenant_registry_id, env_type"

    vps_id = fields.Many2one(
        "tenant.vps",
        required=True,
        ondelete="restrict",
        index=True,
    )
    tenant_registry_id = fields.Many2one(
        "tenant.registry",
        required=True,
        ondelete="cascade",
        index=True,
    )
    env_type = fields.Selection(
        [("dev", "Dev"), ("staging", "Staging"), ("prod", "Production")],
        required=True,
        default="dev",
    )
    db_name = fields.Char(required=True)
    odoo_url = fields.Char()
    addons_revision = fields.Char(
        help="Git SHA of the addons revision currently deployed.",
    )
    last_deploy_id = fields.Char(
        help="Deploy/run id returned by the orchestrator on the last deploy.",
    )
    last_deploy_at = fields.Datetime()

    name = fields.Char(compute="_compute_name", store=True)

    _prod_unique_per_vps = models.Constraint(
        "EXCLUDE (vps_id WITH =) WHERE (env_type = 'prod')",
        "A VPS can host at most one production environment.",
    )
    _env_unique_per_tenant = models.Constraint(
        "unique(tenant_registry_id, env_type)",
        "A tenant can have at most one environment of each type.",
    )

    @api.depends("tenant_registry_id.slug", "env_type")
    def _compute_name(self):
        for rec in self:
            slug = rec.tenant_registry_id.slug or "?"
            rec.name = f"{slug}/{rec.env_type}"

    @api.constrains("env_type", "db_name")
    def _check_db_name(self):
        for rec in self:
            if not rec.db_name or not rec.db_name.strip():
                raise ValidationError(_("db_name is required."))
