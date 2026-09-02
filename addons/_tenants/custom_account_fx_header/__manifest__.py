# -*- coding: utf-8 -*-
{
    "name": "the tenant Foreign-Currency Invoice Header",
    "version": "19.0.1.1.0",
    "summary": "Show the foreign-currency total and the applied exchange rate in the "
    "invoice/bill header and in the Register Payment popup, for every accounting user.",
    "description": """
the tenant Foreign-Currency Invoice Header
========================================
On a customer invoice or vendor bill written in a currency other than the
company currency, the form header gains a summary block::

    Total  CN¥ 20,000.70        Rate  1 CNY = 2,672.29 IDR

Why this module exists
----------------------
Stock Odoo 19 does show ``invoice_currency_rate`` next to the currency in the
header, but:

* it is printed in the "1 <company currency> = N <foreign>" direction
  (``1 IDR = 0.00037421 CNY``), which is unreadable for IDR-based books - the
  number a user actually quotes is ``1 CNY = 2,672.30 IDR``;
* the document total in the foreign currency only appears at the bottom of the
  Invoice Lines tab, not in the header, so an approver has to scroll past all
  the lines to see the amount they are approving;
* the whole native block sits behind ``base.group_multi_currency``, so it
  disappears for any user whose role does not imply that group.

This module adds two non-stored computed helpers on ``account.move``:

``x_fx_is_foreign``
    True when the document currency differs from the company currency and the
    document is an invoice/bill/receipt (never on ``entry``).
``x_fx_rate_company_per_unit``
    The rate inverted into the readable direction: how many units of company
    currency one unit of the document currency is worth
    (``1 / invoice_currency_rate``).

Both are read-only display helpers - nothing about the posting, the amounts or
the rate actually used by the accounting engine is changed. The block carries no
``groups`` restriction, so it survives for users who lack
``base.group_multi_currency``.

Register Payment popup
----------------------
The bank journals here are locked to the company currency, so paying a CNY bill
opens a wizard denominated in IDR. Odoo already converts the CNY residual into
IDR at the payment date, but the popup shows no trace of that, so a user cannot
tell a correct conversion from a missing one. The same block is therefore added
to the wizard::

    Open Amount  CN¥ 20,000.00   Rate  1 CNY = 2,672.29 IDR   Settles  CN¥ 20,000.00

plus a red banner for the one case where the proposed amount really would be the
raw foreign figure: no ``res.currency.rate`` row exists for the document
currency, so ``res.currency._convert`` silently falls back to 1:1.

TENANT-SCOPED: built for the tenant DBs (prd_tenant, trn_tenant).
It is inert on any document whose currency equals the company currency, so it
is harmless anywhere, but it is deliberately kept out of the shared addon paths.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Tenants/the tenant",
    "depends": ["account"],
    "data": [
        "views/account_move_views.xml",
        "views/account_payment_register_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
