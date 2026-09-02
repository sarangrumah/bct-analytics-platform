# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Daily Rollup",
    "summary": "Aggregate successful PPOB transactions into a daily sale.order "
    "+ summary faktur per mitra for e-Faktur / Coretax.",
    "description": """
Custom PPOB Suite - Daily Rollup
================================
Hybrid architecture: real-time ``custom.ppob.transaction`` rows post their own
per-transaction sub-ledger + GL entries during the day. This module rolls the
successful ones up nightly into one ``sale.order`` + summary ``account.move``
(``out_invoice``) per mitra per day, grouped by product.

The summary invoice carries ``x_custom_ppob_is_summary=True`` and posts to a
dedicated journal flagged ``x_custom_report_excluded`` -- so its GL effect is
omitted from the financial reports (Trial Balance / P&L / Balance Sheet). The
real revenue is already booked gross per transaction; the summary document
exists purely for e-Faktur / Coretax NSFP allocation.

Coretax rewiring (vs ERA): NSFP is assigned server-side by DJP under
PER-11/PJ/2025, so this module no longer calls a local NSFP allocator. The
summary invoice is posted and later exported via custom_coretax; rollup
idempotency does not depend on the Coretax status.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
        "sale_management",
        "custom_accounting_reports",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/account_journal_data.xml",
        "data/cron_rollup.xml",
        "views/sale_order_views.xml",
        "views/ppob_transaction_views.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": False,
    "installable": True,
    "auto_install": False,
}
