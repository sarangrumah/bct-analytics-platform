# -*- coding: utf-8 -*-
{
    "name": "Custom Super Admin (Platform Operations)",
    "summary": "Ops-only multi-tenant control plane: provision, suspend, backup, restore tenants",
    "description": """
Super-Admin Vertical for the Custom Odoo 19 Platform
====================================================

Lives at the ``master_admin`` database (accessed via
``https://admin.platform.localhost``). Provides ops + CSM with the UI
needed to drive the tenant lifecycle without ever touching SSH.

Features
--------
- Mirror of ``tenant_registry.tenants`` (master DB) into an Odoo model,
  synced via the tenant-orchestrator REST API every minute.
- Action buttons: Provision, Suspend, Resume, Archive, Trigger Backup,
  Restore-to-Staging — each issues an HMAC-signed call to the
  orchestrator and refreshes the local cache on completion.
- Read-only view of ``tenant_registry.action_log_v`` (append-only,
  hash-chained) for compliance review.
- Backup ledger view with retention info + restore action.
- Linked Grafana per-tenant dashboard (iframe via configurable URL).

Security
--------
All write operations on ``tenant.registry`` are restricted to group
``custom_super_admin.group_super_admin``. The model is read-only via
the ORM otherwise — the source of truth is the master DB registry.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Operations",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "mail"],
    "capability_tags": ["multi-tenant", "audit-trail", "approval-workflow"],
    "external_dependencies": {"python": ["httpx", "croniter"]},
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_sync.xml",
        "data/ir_cron_backup.xml",
        "data/res_config_data.xml",
        "views/tenant_registry_views.xml",
        "views/tenant_action_log_views.xml",
        "views/tenant_backup_views.xml",
        "wizards/tenant_provision_wizard_views.xml",
        "wizards/tenant_restore_wizard_views.xml",
        "wizards/tenant_replicate_wizard_views.xml",
        "views/res_config_settings_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
