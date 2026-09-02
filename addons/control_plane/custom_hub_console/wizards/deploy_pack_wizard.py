# -*- coding: utf-8 -*-
"""Deploy a whole Industry Pack to one or more tenants in a single action.

The wizard delegates to ``custom.hub.industry.pack._deploy_pack``, which
creates one ``custom.hub.module.deployment`` per pack module (deps-first)
and runs the existing canary / deploy flow. No orchestrator logic lives
here — it is fully reused from the deployment model.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomHubDeployPackWizard(models.TransientModel):
    _name = "custom.hub.deploy.pack.wizard"
    _description = "Deploy Industry Pack to Tenant(s) Wizard"

    pack_id = fields.Many2one(
        "custom.hub.industry.pack",
        required=True,
        string="Industry Pack",
    )
    tenant_ids = fields.Many2many(
        "tenant.registry",
        relation="custom_hub_deploy_pack_wizard_tenant_rel",
        column1="wizard_id",
        column2="tenant_id",
        string="Tenants",
        required=True,
    )
    deploy_mode = fields.Selection(
        [
            ("install", "Install"),
            ("upgrade", "Upgrade"),
            ("uninstall", "Uninstall"),
        ],
        required=True,
        default="install",
    )
    enable_canary = fields.Boolean(
        string="Use Canary Rollout",
        default=True,
        help="Run the safe sequence per module: resolve deps, pre-backup, "
        "deploy to canary env, healthcheck, then full rollout (or rollback).",
    )
    skip_installed = fields.Boolean(
        string="Skip Already-Installed",
        default=True,
        help="On install, skip pack modules already marked installed on the "
        "tenant (avoids redundant re-deploys of shared core/compliance modules).",
    )
    confirmed = fields.Boolean(
        string="I confirm",
        help="Required to actually create deployment rows.",
    )
    module_preview = fields.Text(
        string="Modules (resolved, deps-first)",
        compute="_compute_module_preview",
    )

    @api.depends("pack_id")
    def _compute_module_preview(self):
        for rec in self:
            rec.module_preview = ", ".join(rec.pack_id.resolve_module_names()) if rec.pack_id else ""

    def action_confirm(self):
        self.ensure_one()
        if not self.confirmed:
            raise UserError(_("Please tick the confirmation box before proceeding."))
        if not self.tenant_ids:
            raise UserError(_("Select at least one tenant."))
        if not self.pack_id.module_ids:
            raise UserError(_("This pack has no modules. Add modules before deploying."))

        rows = self.pack_id._deploy_pack(
            self.tenant_ids,
            deploy_mode=self.deploy_mode,
            enable_canary=self.enable_canary,
            skip_installed=self.skip_installed,
        )
        if not rows:
            raise UserError(
                _(
                    "Nothing to deploy — every pack module is already installed on "
                    "the selected tenant(s). Untick 'Skip Already-Installed' to force."
                )
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Pack Deployments"),
            "res_model": "custom.hub.module.deployment",
            "view_mode": "list,form",
            "domain": [("id", "in", rows.ids)],
            "target": "current",
        }
