# -*- coding: utf-8 -*-
{
    "name": "Custom Repairs",
    "summary": "Internal asset repairs bridged to maintenance.equipment / maintenance.request",
    "description": """
Custom Repairs extends the CE `repair` module for INTERNAL asset maintenance
(repairs on the company's own equipment, not external-customer jobs):

- Link each repair to an internal asset (maintenance.equipment).
- Bridge: auto-create a corrective maintenance.request on the linked
  equipment when the repair is confirmed, feeding the asset's
  maintenance history (MTBF/MTTR in custom_maintenance).
- Internal fault description and requester (department/user) capture.
- Promised vs actual completion-date tracking + turnaround SLA
  (on_track / at_risk / breached / done).
- Re-open / rework flow for repairs that come back internally.
- Labor + material cost analysis, quality check on completion, and
  optional MRP work-order stub for spare-part consumption.

Convention notes:
- Uses <list>, no <tree>; flat search filters (no <group string="Group By">).
- Booleans in XML use eval="True"/"False".
- Inherits mail.thread for tracking=True fields.
""",
    "author": "Custom Platform",
    "category": "Manufacturing/Repair",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_quality_full",
        "repair",
        "maintenance",
        "mail",
    ],
    "capability_tags": ["maintenance", "quality", "manufacturing"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/repair_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
