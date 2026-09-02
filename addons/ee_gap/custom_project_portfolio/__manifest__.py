# -*- coding: utf-8 -*-
{
    "name": "Custom Project - VAS Portfolio, Verticals & SLA Clock",
    "summary": "Brand verticals, portfolios, weekly sprints, Hold / Waiting-User-Verification "
    "stages with per-stage SLA clock semantics, and weekly progress reports.",
    "description": """
VAS PMO - Portfolio, Verticals & SLA Clock
==========================================
Delta over CE ``project`` for the Product Owner - Value-Added Services team.

Brand verticals
---------------
``custom.project.vertical`` is the brand axis (RETAIL, POS, TENANT, JDS, ...).
Every project and change request carries one; tasks inherit it from their parent and
may only override it with an explicit reason. ``pic_partner_ids`` is the brand-side
contact list -- it is who gets asked when work reaches *Waiting User Verification*.

Per-stage SLA clock
-------------------
The plan called for a ``custom.project.stage.config`` model. It is implemented here as
an **extension of ``project.task.type``** instead: Odoo already owns the stage engine,
and a parallel stage model would mean two kanban implementations and two sources of
truth. The behaviour the plan asked for lives in new fields on that model:

``sla_clock``
    ``running`` (normal), ``paused`` (Hold -- time is deducted from cycle time),
    ``user_side`` (Waiting User Verification -- time keeps running but is booked to the
    user, not to the team), ``stopped`` (closed).

``is_hold`` / ``is_waiting_user`` / ``is_closed`` / ``auto_close_days`` / ``require_reason``

Two honest numbers
------------------
``custom_cycle_time_team`` = elapsed - hold - user-wait. ``custom_lead_time_total`` =
plain elapsed. Reported side by side so a team is never blamed for time it did not own.

Weekly sprints & weekly progress
--------------------------------
``custom.project.sprint`` is one ISO week. A cron closes Friday 18:00, opens the next
week, and moves unfinished work forward as carry-over. ``custom.weekly.progress`` holds
one row per (sprint x project/CR); the automatic half (done / carry-over / hours /
cycle time) is computed, the narrative half (plan, blocker, next week) is written by
the Business Analyst.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Services/Project",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "project",
        "hr_timesheet",
        "mail",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/pmo_groups.xml",
        "security/ir.model.access.csv",
        "security/pmo_rules.xml",
        "data/pmo_stage_data.xml",
        "data/pmo_vertical_data.xml",
        "data/pmo_cron.xml",
        "views/custom_project_vertical_views.xml",
        "views/custom_project_portfolio_views.xml",
        "views/custom_project_sprint_views.xml",
        "views/project_task_type_views.xml",
        "views/project_task_views.xml",
        "views/project_project_views.xml",
        "views/custom_weekly_progress_views.xml",
        "views/pmo_menus.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
