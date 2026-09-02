# -*- coding: utf-8 -*-
{
    "name": "Custom WMS Cycle Count",
    "summary": "Plan-driven cycle counting with variance approval workflow",
    "description": """
Cycle counting / perpetual inventory module.

Supports plan-driven sampling (ABC-velocity, random, by-zone, by-value,
last-counted), session/line model with mail.thread tracking, supervisor
approval gate for variance posting, and a daily cron that generates new
sessions from due plans.
""",
    "author": "Custom Platform",
    "category": "Inventory/Warehouse",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_barcode",
        "stock",
        "product",
        "mail",
    ],
    "capability_tags": ["wms", "barcode-scan", "approval-workflow", "audit-trail", "hht"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/cron.xml",
        "views/cycle_count_plan_views.xml",
        "views/cycle_count_session_views.xml",
        "views/cycle_count_line_views.xml",
        "wizard/cycle_count_start_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
