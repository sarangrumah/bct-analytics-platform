# -*- coding: utf-8 -*-
{
    "name": "Custom Timesheet",
    "summary": "Billable timesheet extensions with payroll OT integration",
    "description": """
Custom Timesheet extends standard hr_timesheet/project to support billable
hours tracking, billing rate per analytic line, and an integration hook
toward payroll overtime work entries (custom_hr_payroll_id).

Phase 2E scope:
- account.analytic.line: billable flag, billing rate/currency, overtime hours
- Validation workflow (draft/submitted/validated) via custom_approval_engine
- Link from analytic line to billed invoice line
- Wizard to create draft customer invoice from billable timesheets
- Overtime -> hr.work.entry creation (CE hr_work_entry)
- AI weekly summary per project (custom.timesheet.weekly.summary)
- Pivot view (employee x date)
""",
    "author": "Custom Platform",
    "category": "Services/Timesheets",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_approval_engine",
        "custom_ai_bridge",
        "hr_timesheet",
        "project",
        "account",
        "sale_management",
        "sale_timesheet",
        "hr_work_entry",
        "custom_hr_payroll_id",
        "mail",
    ],
    "capability_tags": ["timesheet", "approval-workflow", "payroll", "ai", "accounting"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/account_analytic_line_views.xml",
        "views/sale_order_views.xml",
        "views/custom_timesheet_weekly_summary_views.xml",
        "views/custom_timesheet_invoice_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
