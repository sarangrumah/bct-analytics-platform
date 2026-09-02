# -*- coding: utf-8 -*-
"""dev.cycle.deployment — link a dev cycle to an environment deployment."""

from __future__ import annotations

from odoo import fields, models


class DevCycleDeployment(models.Model):
    _name = "dev.cycle.deployment"
    _description = "Dev Cycle Deployment"
    _order = "deployed_at desc, id desc"

    cycle_id = fields.Many2one(
        "dev.cycle",
        string="Dev Cycle",
        required=True,
        ondelete="cascade",
        index=True,
    )
    module_deployment_id = fields.Many2one(
        comodel_name="custom.hub.module.deployment",
        string="Module Deployment",
        ondelete="set null",
    )
    environment_id = fields.Many2one(
        comodel_name="tenant.environment",
        string="Environment",
        ondelete="set null",
    )
    deployed_at = fields.Datetime(default=fields.Datetime.now)
    outcome = fields.Selection(
        [
            ("success", "Success"),
            ("failure", "Failure"),
            ("rolled_back", "Rolled Back"),
        ],
        default="success",
        index=True,
    )
