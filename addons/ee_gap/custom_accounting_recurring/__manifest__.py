# -*- coding: utf-8 -*-
{
    "name": "Custom Accounting Recurring",
    "summary": "Recurring journal entries and recurring payments (EE-gap fulfillment).",
    "description": """
Custom Accounting Recurring
===========================

Adds two recurrence engines on top of Odoo CE ``account``:

* ``custom.recurring.journal.template`` — schedule balanced journal
  entries (e.g. monthly depreciation, lease accruals) and post them
  automatically via a daily cron, optionally pre-posted.
* ``custom.recurring.payment.template`` — schedule outbound / inbound
  customer or vendor payments.

Both engines respect ``end_date`` and stop generating once the next
run would fall after it. Generated moves are audited through
``custom_pdp_audit``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_accounting_full",
        "account",
    ],
    "capability_tags": ["accounting", "recurring", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/cron.xml",
        "views/recurring_template_views.xml",
        "views/recurring_payment_template_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
