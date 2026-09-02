# -*- coding: utf-8 -*-
{
    "name": "Custom Maintenance",
    "summary": "IoT alerts, MTBF/MTTR, predictive scheduling, SLA, spare parts and cost tracking",
    "description": """
Custom Maintenance extends the CE `maintenance` module with:
- IoT-triggered alert thresholds on equipment, integrated with custom_iot_bridge
- Auto-creation of maintenance.request records when sensor metrics breach thresholds
- MTBF / MTTR reliability metrics computed from request history
- Predictive maintenance scheduling based on MTBF and IoT signals
- Spare parts catalogue per request with optional stock.move integration
- Maintenance team SLA policies (response/resolve deadlines) with breach cron
- Cost tracking (labor + parts) per request
- PDP audit logging on equipment owner / user changes
""",
    "author": "Custom Platform",
    "category": "Manufacturing/Maintenance",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "maintenance",
        "custom_iot_bridge",
        "product",
        "mail",
    ],
    "capability_tags": ["iot", "anomaly-detection", "audit-trail", "approval-workflow"],
    "data": [
        "security/custom_maintenance_security.xml",
        "security/ir.model.access.csv",
        "data/maintenance_iot_cron.xml",
        "data/maintenance_sla_cron.xml",
        "views/custom_maintenance_team_sla_views.xml",
        "views/maintenance_equipment_views.xml",
        "views/maintenance_request_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
