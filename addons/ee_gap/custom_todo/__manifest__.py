# -*- coding: utf-8 -*-
{
    "name": "Custom Todo",
    "summary": "Personal To-Do extensions: pomodoro timer, AI task breakdown, recurring templates",
    "description": """
Custom Todo extends the CE ``project_todo`` application documented at
https://www.odoo.com/documentation/19.0/applications/productivity/todo.html.

Adds:
- Pomodoro timer state on every task (focus/break/done with configurable durations)
- AI-powered task breakdown via ``custom_ai_bridge``
- Recurring template tasks (daily/weekly/monthly) usable as boilerplate
""",
    "author": "Custom Platform",
    "category": "Productivity/To-Do",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "project_todo",
    ],
    "capability_tags": ["ai", "audit-trail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/project_task_views.xml",
        "views/custom_todo_template_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
