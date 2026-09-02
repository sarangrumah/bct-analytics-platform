# -*- coding: utf-8 -*-
{
    "name": "Custom Fleet ID",
    "summary": "Indonesia fleet localization: STNK & KIR reminders, BBM tracking, driver assignment with PDP audit",
    "description": """
Custom Fleet ID extends the standard Odoo Fleet app with Indonesian
regulatory requirements:

- STNK (Surat Tanda Nomor Kendaraan) number + expiry tracking with configurable alert window
- KIR (Kartu Uji Berkala) number + expiry tracking with configurable alert window
- BBM (fuel) type selection covering Pertamina + EV
- Driver assignment (res.partner) with chatter tracking + PDP audit log
- Daily cron posting reminders to vehicles whose STNK is expiring or expired

EE-equivalent extensions:
- Auto-create maintenance.request for STNK/KIR renewal when within alert window (if `maintenance` is installed)
- BBM fuel log (custom.fleet.bbm.log) with km/L consumption compute
- Driver assignment history (custom.fleet.driver.assignment) auto-managed on driver change
- Next service km/date tracking + cron flag for due services
- Indonesia plate format validator (warning, non-blocking)
""",
    "author": "Custom Platform",
    "category": "Human Resources/Fleet",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "fleet",
        "mail",
    ],
    "capability_tags": ["fleet", "pdp", "audit-trail"],
    "data": [
        "security/custom_fleet_id_security.xml",
        "security/ir.model.access.csv",
        "data/fleet_id_cron.xml",
        "views/fleet_vehicle_views.xml",
        "views/custom_fleet_bbm_log_views.xml",
        "views/custom_fleet_driver_assignment_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
