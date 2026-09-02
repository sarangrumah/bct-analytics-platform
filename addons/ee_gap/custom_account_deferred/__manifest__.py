# -*- coding: utf-8 -*-
{
    "name": "Custom Deferred Revenue & Expense",
    "summary": "EE-style deferred entries: spread invoice/bill lines over "
    "a date range via a deferred account (EE gap closure).",
    "description": """
Custom Deferred Revenue & Expense
=================================

Closes the EE-gap on deferred entry management (EE 17+ style):

* ``Start Date`` / ``End Date`` on invoice & bill lines.
* On posting, one *deferral* entry reclasses the P&L amount to the
  company's deferred expense/revenue account, then one *recognition*
  entry per month moves it back, day-count prorated (rounding remainder
  lands on the last month). Future recognitions are created draft with
  ``auto_post='at_date'`` — core's autopost cron posts them, no custom
  cron.
* Resetting/reversing the origin cleans up its generated entries.
* Deferred schedule report (per account, per month).

Configure the deferred accounts + journal in Settings → Accounting.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_move_views.xml",
        "views/res_config_settings_views.xml",
        "views/deferred_views.xml",
    ],
    "installable": True,
    "application": False,
}
