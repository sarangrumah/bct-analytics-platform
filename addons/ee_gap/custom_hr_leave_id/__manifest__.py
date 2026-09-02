# -*- coding: utf-8 -*-
{
    "name": "Custom HR Leave Indonesia",
    "summary": "Indonesian localization for HR Time Off (UU Cipta Kerja, cuti haid, public holidays, carry-over)",
    "description": """
Indonesian localization for Time Off / hr.leave:
- Maternity leave 6 months (UU Cipta Kerja No. 6/2023)
- Cuti haid (menstrual leave) 2 days/month
- Cuti tahunan, cuti besar, cuti alasan penting, cuti di luar tanggungan
- ID public holiday master seeded 2024-2026 (static, best-effort)
- Holiday overlap warning on leave requests
- Carry-over policy + cron stub
- Auto annual leave allocation on employee hire (pro-rated)
- SQL view: leave balance report (allocated / used / remaining per year)
- Integration with custom_approval_engine via approval.mixin
- Audit trail via custom_pdp_audit
""",
    "author": "Custom Platform",
    "category": "Human Resources/Time Off",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "hr",
        "hr_holidays",
        "custom_approval_engine",
        "mail",
    ],
    "capability_tags": ["indonesian-hr", "approval-workflow", "audit-trail", "pdp"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/id_leave_types.xml",
        "data/id_public_holiday_2024.xml",
        "data/id_public_holiday_2025.xml",
        "data/id_public_holiday_2026.xml",
        "data/id_public_holiday_cron.xml",
        "views/id_public_holiday_views.xml",
        "views/hr_leave_type_views.xml",
        "views/hr_leave_views.xml",
        "views/custom_leave_carryover_policy_views.xml",
        "views/custom_leave_balance_report_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
