# -*- coding: utf-8 -*-
"""Extension of ``tenant.registry`` (from ``custom_super_admin``).

Adds vertical/business metadata, deployment topology, assigned-module
links, and a computed ``health_status`` that pulls from
``custom_ops_monitor`` when installed (otherwise reports ``unknown``).

All optional-sibling-module integrations go through
``_hub_is_module_installed`` so the field still works on a minimal
tenant.
"""

from __future__ import annotations

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class TenantRegistryHub(models.Model):
    _inherit = "tenant.registry"

    business_domain = fields.Selection(
        [
            ("rental", "Rental"),
            ("manufacturing", "Manufacturing"),
            ("retail", "Retail"),
            ("services", "Services"),
            ("government", "Government"),
            ("finance", "Finance"),
            ("healthcare", "Healthcare"),
            ("logistics", "Logistics"),
            ("ppob", "PPOB"),
            ("wms", "WMS"),
            ("other", "Other"),
        ],
        string="Business Domain",
        default="other",
        index=True,
        tracking=True,
    )

    deployment_topology = fields.Selection(
        [
            ("centralized", "Centralized (Hub-only)"),
            ("on_prem_bridged", "On-Prem Bridged"),
            ("hybrid", "Hybrid"),
        ],
        string="Deployment Topology",
        default="centralized",
        index=True,
        tracking=True,
    )

    vpn_endpoint = fields.Char(
        string="VPN / Bridge Endpoint",
        help="Bridge endpoint (host:port) for on_prem_bridged topology.",
    )

    # ----- Assigned modules (optional brd_analyzer link) -----
    assigned_module_ids = fields.Many2many(
        comodel_name="ir.module.module",
        relation="custom_hub_tenant_module_rel",
        column1="tenant_id",
        column2="module_id",
        string="Assigned Modules",
        help="Modules earmarked for this tenant (planned or installed).",
    )
    assigned_capability_ids = fields.Many2many(
        comodel_name="custom.module.capability.entry",
        relation="custom_hub_tenant_capability_rel",
        column1="tenant_id",
        column2="capability_id",
        string="Assigned Capabilities",
        help="BRD-analyzer capability entries assigned to this tenant. "
        "Populated only when custom_brd_analyzer is installed.",
    )
    module_count = fields.Integer(
        string="Module Count",
        compute="_compute_module_count",
        store=False,
    )

    # ----- Health (computed from custom_ops_monitor if present) -----
    health_status = fields.Selection(
        [
            ("green", "Green"),
            ("yellow", "Yellow"),
            ("red", "Red"),
            ("unknown", "Unknown"),
        ],
        string="Health",
        compute="_compute_health_status",
        store=False,
    )
    last_incident_id = fields.Many2one(
        comodel_name="custom.ops.incident",
        string="Last Incident",
        compute="_compute_last_incident",
        store=False,
    )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    @api.model
    def _hub_is_module_installed(self, name):
        """Return True if the given technical module is installed."""
        return bool(
            self.env["ir.module.module"].sudo().search_count([("name", "=", name), ("state", "=", "installed")])
        )

    # -----------------------------------------------------------------
    # Computes
    # -----------------------------------------------------------------
    @api.depends("assigned_module_ids", "assigned_capability_ids")
    def _compute_module_count(self):
        for rec in self:
            rec.module_count = len(rec.assigned_module_ids) + len(rec.assigned_capability_ids)

    def _compute_health_status(self):
        has_health = self._hub_is_module_installed("custom_ops_monitor")
        Health = self.env["custom.ops.tenant.health"] if has_health else None
        for rec in self:
            if not has_health or not rec.id:
                rec.health_status = "unknown"
                continue
            try:
                latest = Health.sudo().search(
                    [("tenant_id", "=", rec.id)],
                    order="create_date desc",
                    limit=1,
                )
                rec.health_status = getattr(latest, "status", False) or "unknown"
            except Exception as exc:  # pragma: no cover - defensive
                _logger.debug("[hub] health compute failed: %s", exc)
                rec.health_status = "unknown"

    def _compute_last_incident(self):
        has_inc = self._hub_is_module_installed("custom_ops_monitor")
        Inc = self.env["custom.ops.incident"] if has_inc else None
        for rec in self:
            if not has_inc or not rec.id:
                rec.last_incident_id = False
                continue
            try:
                last = Inc.sudo().search(
                    [("tenant_id", "=", rec.id)],
                    order="create_date desc",
                    limit=1,
                )
                rec.last_incident_id = last.id if last else False
            except Exception:
                rec.last_incident_id = False
