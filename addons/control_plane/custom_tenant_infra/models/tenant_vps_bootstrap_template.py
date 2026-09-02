# -*- coding: utf-8 -*-
"""``tenant.vps.bootstrap.template`` — versioned shell-script templates.

Stored as ``ir.attachment`` so they can be edited by ops without a code
deploy. Variables are rendered as jinja2 (``{{ vps_hostname }}``, etc.)
by the orchestrator before being scp'd to the VPS.
"""

from __future__ import annotations

from odoo import fields, models


SCRIPT_KIND_SELECTION = [
    ("harden_os", "Harden OS"),
    ("install_docker", "Install Docker"),
    ("install_caddy", "Install Caddy"),
    ("deploy_odoo", "Deploy Odoo Stack"),
]


class TenantVpsBootstrapTemplate(models.Model):
    _name = "tenant.vps.bootstrap.template"
    _description = "Tenant VPS Bootstrap Template"
    _order = "script_kind, version desc"
    _rec_name = "name"

    name = fields.Char(required=True)
    version = fields.Char(default="1.0.0", required=True)
    script_kind = fields.Selection(SCRIPT_KIND_SELECTION, required=True, index=True)
    script_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Script Attachment",
        required=True,
        ondelete="restrict",
        help="Attachment holding the (jinja2) shell script body.",
    )
    variables_json = fields.Json(
        string="Default Variables",
        help="Default jinja2 variables merged with per-VPS values at render time.",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    _kind_version_unique = models.Constraint(
        "unique(script_kind, version)",
        "A script kind+version pair must be unique.",
    )
