# -*- coding: utf-8 -*-
{
    "name": "Custom Hub Control Center",
    "summary": "Unified hub: tenants, modules, monitoring, BRD, HHT, AI, audit",
    "description": """
Hub Control Center
==================

Top-level wrapper module that brings together every operational surface
of the platform under a single navigation tree:

* Tenants & Verticals (extends ``custom_super_admin``)
* Module Catalog with deploy-to-tenant wizard
* Health & Capacity dashboards (passthrough to ``custom_ops_monitor``)
* BRD Analyzer (passthrough to ``custom_brd_analyzer``)
* HHT Device admin (passthrough to ``custom_hht_bridge``)
* AI Console (passthrough to ``custom_ai_features``)
* Cross-source Audit Log

Optional sibling modules (``custom_ops_monitor``, ``custom_brd_analyzer``,
``custom_hht_bridge``) are detected at runtime; their cards and menus
appear only when installed. The module installs cleanly on a minimal
tenant containing only ``custom_core`` + ``custom_super_admin`` +
``custom_ai_features``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Hub",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_super_admin",
        "custom_ai_features",
        # Sibling modules whose models are referenced by Hub fields
        # (capability tags, ops incidents, tenant health). They ship in
        # the same platform, so we treat them as hard prerequisites.
        "custom_brd_analyzer",
        "custom_ops_monitor",
        "mail",
    ],
    "capability_tags": ["multi-tenant", "audit-trail", "approval-workflow", "ai"],
    "data": [
        # security first
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        # data / seeds / crons
        "data/ir_config_parameter_seed.xml",
        "data/cron.xml",
        "data/industry_pack_seed.xml",
        # views
        "views/module_catalog_views.xml",
        "views/module_deployment_views.xml",
        "views/industry_pack_views.xml",
        "views/audit_event_views.xml",
        "views/ai_usage_views.xml",
        "views/vertical_tenant_extension_views.xml",
        "views/hub_dashboard_action.xml",
        # wizards
        "wizards/deploy_module_wizard_views.xml",
        "wizards/tenant_provision_wizard_views.xml",
        # menus (must come AFTER actions they reference)
        "views/menu_views.xml",
        # genesis audit event — runs last so create() can compute hash
        "data/audit_event_seed.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_hub_console/static/src/js/hub_dashboard/hub_dashboard.js",
            "custom_hub_console/static/src/js/hub_dashboard/hub_dashboard.xml",
            "custom_hub_console/static/src/js/hub_dashboard/hub_dashboard.scss",
        ],
    },
    "post_init_hook": "_post_install_link_menus",
    "installable": True,
    "application": True,
    "auto_install": False,
}
