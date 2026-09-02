# -*- coding: utf-8 -*-
"""Inject ``environment_id`` into hub_console canary fields.

custom_hub_console can't depend on custom_tenant_infra (the reverse is
true), so the M2O to ``tenant.environment`` lives here via _inherit.
"""

from odoo import fields, models


class HubModuleDeploymentEnv(models.Model):
    _inherit = "custom.hub.module.deployment"

    environment_id = fields.Many2one(
        comodel_name="tenant.environment",
        string="Target Environment",
        ondelete="set null",
        help="Optional staging/prod environment row from tenant_infra. "
        "When unset the deployment targets the tenant default.",
    )


class HubDeployModuleWizardEnv(models.TransientModel):
    _inherit = "custom.hub.deploy.module.wizard"

    target_environment_id = fields.Many2one(
        comodel_name="tenant.environment",
        string="Canary Environment",
        ondelete="set null",
        help="Optional explicit staging environment to target. If empty "
        "the deployment auto-picks a staging env for each tenant.",
    )
