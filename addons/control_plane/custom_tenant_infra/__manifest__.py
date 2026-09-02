# -*- coding: utf-8 -*-
{
    "name": "Custom Tenant Infra",
    "summary": "VPS lifecycle + auto-deploy (SSH bootstrap, Docker, Caddy, Odoo stack)",
    "description": """
Tenant Infrastructure
=====================

Manage the per-tenant VPS fleet end-to-end from Odoo:

- ``tenant.vps`` — VPS inventory + state machine
  (``registered`` → ``hardening`` → ``bootstrapping`` → ``active``
  → ``degraded`` → ``decommissioned``).
- ``tenant.environment`` — per-environment (dev/staging/prod) deployment
  metadata linked to a ``tenant.registry`` row. ``prod`` is 1:1 with a VPS.
- ``tenant.vps.bootstrap.template`` — versioned shell-script templates
  (jinja2 placeholders) stored as ``ir.attachment``.

Action buttons on the form call the FastAPI orchestrator
(``/v1/vps/...``, HMAC-signed) which SSHs into the VPS using a credential
reference (``vault://...``). Credentials are NEVER stored in Odoo.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_super_admin",
        "custom_ops_monitor",
        "custom_hub_console",
    ],
    "capability_tags": ["multi-tenant", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        # security
        "security/security.xml",
        "security/ir.model.access.csv",
        # views
        "views/tenant_vps_views.xml",
        "views/tenant_environment_views.xml",
        "views/tenant_vps_bootstrap_template_views.xml",
        "views/hub_deployment_inherit_views.xml",
        "views/menu_views.xml",
        # data (seed templates last so views/actions exist)
        "data/bootstrap_templates_data.xml",
    ],
    "demo": [
        "data/demo_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
