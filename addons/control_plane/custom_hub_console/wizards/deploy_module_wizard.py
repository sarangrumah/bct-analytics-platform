# -*- coding: utf-8 -*-
"""Deploy a catalog module to one or more tenants.

The wizard creates ``custom.hub.module.deployment`` rows and, when
``confirmed`` is checked, immediately fires ``action_deploy()`` on each.
Orchestrator errors are caught inside the deployment model so the wizard
always commits — failures surface on the deployment rows themselves.

Track C: optional canary flow. When ``enable_canary`` is true the wizard
runs the sequence: resolve deps → pre-backup → deploy canary → health-
check → rollout full (or rollback on failure). The sequence is executed
synchronously inside the wizard — for MVP we accept the latency cost
rather than wiring a queue.job dependency.
"""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomHubDeployModuleWizard(models.TransientModel):
    _name = "custom.hub.deploy.module.wizard"
    _description = "Deploy Module to Tenant(s) Wizard"

    catalog_id = fields.Many2one(
        "custom.hub.module.catalog",
        required=True,
        string="Module",
    )
    tenant_ids = fields.Many2many(
        "tenant.registry",
        relation="custom_hub_deploy_wizard_tenant_rel",
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
    schedule_at = fields.Datetime(
        help="If set, deployments are recorded but not executed now; an "
        "external scheduler / ops process is expected to pick them up."
    )
    confirmed = fields.Boolean(
        string="I confirm",
        help="Required to actually create deployment rows.",
    )
    note = fields.Text()

    # Track C
    enable_canary = fields.Boolean(
        string="Use Canary Rollout",
        default=True,
        help="Run the safe sequence: resolve deps, pre-backup, deploy to "
        "canary env, healthcheck, then full rollout (or rollback).",
    )
    # target_environment_id (M2O to tenant.environment) injected by
    # custom_tenant_infra. See hub_module_deployment_extension.py.

    def action_confirm(self):
        self.ensure_one()
        if not self.confirmed:
            raise UserError(_("Please tick the confirmation box before proceeding."))
        if not self.tenant_ids:
            raise UserError(_("Select at least one tenant."))

        Deployment = self.env["custom.hub.module.deployment"].sudo()
        rows = Deployment
        for tenant in self.tenant_ids:
            vals = {
                "catalog_id": self.catalog_id.id,
                "tenant_id": tenant.id,
                "deploy_mode": self.deploy_mode,
                "state": "pending",
            }
            tgt_env = self["target_environment_id"] if "target_environment_id" in self._fields else False
            if tgt_env:
                vals["environment_id"] = tgt_env.id
            row = Deployment.create(vals)
            rows |= row

        # Fire immediately unless scheduled for later.
        if not self.schedule_at:
            if self.enable_canary:
                rows._run_canary_sequence()
            else:
                rows.action_deploy()

        return {
            "type": "ir.actions.act_window",
            "name": "Deployments",
            "res_model": "custom.hub.module.deployment",
            "view_mode": "list,form",
            "domain": [("id", "in", rows.ids)],
            "target": "current",
        }


class CustomHubModuleDeploymentCanary(models.Model):
    """Helper extension to bundle the canary sequence on the deployment.

    Defined alongside the wizard so the file boundary mirrors the feature
    boundary; logically still part of the deployment model.
    """

    _inherit = "custom.hub.module.deployment"

    def _run_canary_sequence(self):
        """Run resolve → pre-backup → canary → healthcheck → full/rollback."""
        for rec in self:
            try:
                rec.action_resolve_dependencies()
                rec.action_take_pre_backup()
                rec.action_deploy_canary()
                if rec.state == "failed":
                    # Canary itself errored — try rollback if we have a snap.
                    if rec.rollback_snapshot_id:
                        rec.action_rollback()
                    continue
                rec.action_healthcheck()
                if rec.healthcheck_passed:
                    rec.action_rollout_full()
                else:
                    rec.error_message = rec.error_message or "Healthcheck did not pass; rolling back."
                    if rec.rollback_snapshot_id:
                        rec.action_rollback()
                    else:
                        rec.canary_phase = "rolled_back"
                        rec.state = "failed"
            except Exception as exc:  # noqa: BLE001
                _logger.exception("[hub_deploy] canary sequence aborted: %s", exc)
                rec.error_message = f"Canary sequence aborted: {exc}"
                rec.state = "failed"
        return True
