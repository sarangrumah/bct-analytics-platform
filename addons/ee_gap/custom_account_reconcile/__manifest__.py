# -*- coding: utf-8 -*-
{
    "name": "Custom Account Reconciliation",
    "summary": "Enterprise-style manual reconciliation menu and wizard for Odoo CE (EE-gap closure).",
    "description": """
Custom Account Reconciliation
=============================

Closes the EE-gap on Odoo CE ``account_accountant`` manual
reconciliation:

* **Accounting → Accounting → Reconciliation → Reconcile** — overview of
  every reconcilable account that still carries open (unreconciled)
  journal items, with counts and residual balance. Click through to the
  open items of one account.
* **Reconcile** action on Journal Items (list-view action menu) — select
  matching debit/credit lines and reconcile them in one click, with an
  optional write-off entry when the selection does not net to zero.

The heavy lifting is core CE ``account.move.line.reconcile()`` (partial
reconciliation, matching numbers, exchange differences) — this module
only supplies the missing UI.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.2.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/reconcile_views.xml",
        "views/bank_reconcile_views.xml",
    ],
    "installable": True,
    "application": False,
}
