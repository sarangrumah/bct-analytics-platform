# -*- coding: utf-8 -*-
{
    "name": "Custom Operational Reports",
    "version": "19.0.0.1.0",
    "summary": "Operational reports for the drone fleet — asset opname, "
    "event movement, spare parts, maintenance health, repair history.",
    "description": """
Custom Operational Reports
==========================
Five operational reports for the Inventory / warehouse team, built on the
shared ``custom.report.engine`` so each one renders as an on-screen table and an
Excel export from a single column/line contract (no PDF — these are working
lists, not signed documents):

* **Asset Opname** (#15) — the drone fleet register with accounting state,
  best-effort operational state (rental) and condition (latest BAST).
* **Event Movement** (#16) — in/out unit & tool movement per rental event.
* **Spare Parts** (#17) — spare availability (stock) + usage from maintenance.
* **Maintenance Health** (#18) — per-equipment request counts, MTBF/MTTR, cost.
* **Repair History** (#19) — repair.order history with SLA, rework and cost.

Depends on the operational apps that own the data, so it only installs where the
fleet is actually operated in Odoo.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Operations/Reports",
    # Generic modules only. Depending on a tenant module (e.g.
    # custom_asset_register_seed) would invert the layering and drag that
    # tenant's data seed into any database that just wanted the reports.
    # `serial_number` is contributed by the tenant register and is read
    # defensively, so these reports install and run without it.
    "depends": [
        "custom_accounting_reports",
        "custom_accounting_asset",
        "custom_rental",
        "custom_maintenance",
        "custom_repairs",
        "custom_bast",
        "stock",
    ],
    "data": [
        "security/ir.model.access.csv",
        "wizard/asset_opname_wizard_views.xml",
        "wizard/event_movement_wizard_views.xml",
        "wizard/spareparts_wizard_views.xml",
        "wizard/maintenance_health_wizard_views.xml",
        "wizard/repair_history_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
