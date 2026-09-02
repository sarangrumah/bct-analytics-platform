# -*- coding: utf-8 -*-
{
    "name": "Custom Dashboards",
    "summary": "Lightweight tile-based KPI dashboards with AI NLQ",
    "description": """
Custom Dashboards is a CE-targeted reimplementation of the EE
``board`` dashboard builder.

Phase 2E:
- Dashboards with publishing + ACLs (allowed_group_ids) + public share link
- Tiles: count, sum, avg, last_value, formula, chart_bar, chart_pie
- Per-tile cached values (JSON), cron-driven refresh, configurable interval
- Drill-down from tile to underlying records
- Wizard for tile creation with model/field pickers and preview
- "Ask AI" NLQ via custom_ai_bridge (custom.ai._recommend)
- Public read-only share endpoint /custom_dashboard/share/<token>
- PDP audit logging on all changes
""",
    "author": "Custom Platform",
    "category": "Productivity/Dashboards",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "board",
    ],
    "capability_tags": ["ai", "audit-trail", "pdp"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/dashboard_cron.xml",
        "views/custom_dashboard_tile_views.xml",
        "views/custom_dashboard_views.xml",
        "views/custom_dashboard_tile_wizard_views.xml",
        "views/share_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
