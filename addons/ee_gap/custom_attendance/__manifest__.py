# -*- coding: utf-8 -*-
{
    "name": "Custom Attendance",
    "summary": "Geofence check-in, kiosk portal, approval workflow, OT to payroll",
    "description": """
Custom Attendance extends hr_attendance with:

- Geofence definitions (lat/lng + radius) for mobile-friendly check-in
- Capture of check-in / check-out GPS coordinates on attendance records
- Haversine-based validation of check-in coordinates against the assigned geofence
- Configurable overtime rules (threshold, multiplier, weekday/weekend/holiday)
- Automatic overtime computation and hr.work.entry generation feeding payroll
- Manual approval workflow for anomalies (long shifts, late-night check-ins)
- Public kiosk portal (/custom_attendance/kiosk) with PIN-based toggle
- Face recognition stub bridged to custom.ai gateway
- PDP audit trail for attendance lifecycle events
""",
    "author": "Custom Platform",
    "category": "Human Resources/Attendances",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "hr_attendance",
        "hr_work_entry",
        "custom_hr_payroll_id",
        "portal",
        "mail",
    ],
    "capability_tags": ["attendance", "geofence", "kiosk", "approval-workflow", "payroll", "ai"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/attendance_overtime_rule_seed.xml",
        "views/attendance_geofence_views.xml",
        "views/custom_attendance_overtime_rule_views.xml",
        "views/hr_attendance_views.xml",
        "views/kiosk_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
