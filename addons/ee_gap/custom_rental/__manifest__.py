# -*- coding: utf-8 -*-
{
    "name": "Custom Rental",
    "summary": "Asset rental lifecycle: pricing tiers, schedule, BAST, late fees, portal, stock pickings",
    "description": """
Full-featured rental management on top of Odoo 19 Community. Extends the
core rental.asset / rental.order skeleton with:

* Per-period pricing tiers (hour / day / week / month)
* Calendar + kanban + list schedule view (SQL view backed)
* Portal /my/rentals with signature capture and PDF contract
* BAST pickup / return document generation (custom_bast module)
* Daily late-fee accrual cron with per-day audit lines
* QWeb-PDF rental contract
* Optional stock.picking integration on confirm / return
""",
    "author": "Custom Platform",
    "category": "Sales/Rental",
    "version": "19.0.0.3.1",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_bast",
        "mail",
        "portal",
        "product",
        "stock",
        "account",
    ],
    "capability_tags": ["rental", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/cron_data.xml",
        "views/rental_asset_views.xml",
        "views/rental_pricing_views.xml",
        "views/rental_schedule_views.xml",
        "views/rental_order_views.xml",
        "views/custom_bast_views.xml",
        "views/portal_templates.xml",
        "views/res_config_settings_views.xml",
        "views/menu_views.xml",
        "report/rental_contract_report.xml",
        "reports/bast_report_loan.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
