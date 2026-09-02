# -*- coding: utf-8 -*-
{
    "name": "Custom Planning",
    "summary": "Resource planning + shift scheduling for SMB teams",
    "description": """
Lightweight resource planning. Define planning roles, assign shifts to
employees, detect overlapping bookings, and surface workload via a
gantt-style kanban grouped by day.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Planning",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": ["custom_core", "custom_pdp_audit", "hr", "mail"],
    "capability_tags": ["planning", "shift-scheduling", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/planning_role_views.xml",
        "views/planning_slot_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
