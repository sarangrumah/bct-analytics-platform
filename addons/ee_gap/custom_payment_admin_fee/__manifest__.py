# -*- coding: utf-8 -*-
{
    "name": "Payment Admin Fees",
    "version": "19.0.1.1.0",
    "summary": "Multi-COA admin/bank fee lines on the Register Payment wizard.",
    "description": """
Payment Admin Fees
==================
The Register Payment wizard gains an *Admin Fees* section where one or more fee
lines (each with its own COA, label and amount) can be added on top of the
bill/invoice being settled.

A 1,000,000 vendor bill with a 1,500 bank admin fee produces a 1,001,500
cash-out::

    Dr  Accounts Payable (counterpart)   1,000,000   -> reconciles the bill
    Dr  Bank Admin Charges (fee COA)         1,500   -> one line per fee COA
    Cr  Bank (liquidity)                             1,001,500

The fees ride Odoo's native payment *write-off* channel, so the counterpart
(``amount - fees``) still equals the bill residual and the bill reconciles in
full. The fee lines are ordinary journal items, so they also show up on any
payment voucher/receipt printout.

A negative amount is allowed: the fee is then netted *off* the settlement
(cash-in lower than the receivable), which is how acquirer/transfer charges on
customer receipts are usually booked.

NOTE: this is the tenant-neutral extraction of the same feature in
``custom_retail_localization`` (which adds card-BIN/MDR and Operating-Unit
handling on top). Do NOT install both on the same database -- the Admin Fees
section would appear twice.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_payment_register_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
