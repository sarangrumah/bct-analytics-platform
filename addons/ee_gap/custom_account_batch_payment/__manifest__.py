# -*- coding: utf-8 -*-
{
    "name": "Custom Batch Payments",
    "summary": "Batch payments with Indonesian bank transfer-file export (EE account_batch_payment gap closure).",
    "description": """
Custom Batch Payments
=====================

Closes the EE-gap on Odoo CE ``account_batch_payment``:

* Group posted ``account.payment`` records of one bank journal into a
  batch (draft → validated → sent → reconciled).
* Export a bank transfer file per batch. Formats are pluggable records
  (``custom.batch.payment.format``) mirroring the
  ``custom.bank.import.template`` pattern — seeded with BCA MCM,
  Mandiri MCM, BNI and BRI CSV layouts, refine against the corporate
  H2H contract's real sample files before freezing.
* Smart button + "Add to Batch" action on payments; batch turns
  *reconciled* once every payment is paid & matched with a statement.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/batch_payment_sequence.xml",
        "data/batch_export_formats.xml",
        "views/account_batch_payment_views.xml",
        "views/account_payment_views.xml",
    ],
    "installable": True,
    "application": False,
}
