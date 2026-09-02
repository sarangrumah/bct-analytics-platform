# -*- coding: utf-8 -*-
{
    "name": "Custom Lunch (Indonesia) EE",
    "summary": (
        "EE-equivalent lunch: live GoFood/GrabFood/ShopeeFood links, real payroll "
        "deduction, halal badge, spice filters, daily auto-publish, weekly calorie report"
    ),
    "description": """
Enterprise-equivalent extension on top of the CE `lunch` module:

* Live vendor app links (GoFood / GrabFood / ShopeeFood) computed from
  merchant IDs, with an "Open" button on the supplier form.
* Real payroll deduction: monthly cron aggregates confirmed lunch orders
  marked ``x_payroll_deduction`` and posts a "Lunch Deduction" line on each
  employee's draft ``hr.payslip`` for the matching period, linking back via
  ``x_payslip_id``.
* Halal certification badge on the product list + "Halal Only" search filter.
* Per-level spice search filters (mild / medium / hot / very hot).
* Daily menu auto-publish: cron toggles ``active`` on ``lunch.product`` based
  on a comma-separated ``x_available_days`` schedule (mon,tue,...).
* Weekly calorie summary per employee via SQL-view model + dedicated report.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Lunch",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "lunch",
        "custom_hr_payroll_id",
    ],
    "capability_tags": ["lunch", "payroll", "halal", "indonesian-hr"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/lunch_cron.xml",
        "views/lunch_supplier_views.xml",
        "views/lunch_product_views.xml",
        "views/lunch_order_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
