{
    "name": "Accounting / Invoicing Menu Split",
    "summary": "Separate Accounting from Invoicing, the way Enterprise presents them",
    "description": """
Odoo Community ships ONE accounting application, `account.menu_finance`, labelled
"Invoicing". Accounting is not a peer of it -- it is the `menu_finance_entries`
submenu *inside* it. Everything an accountant does (journal entries, closing,
review, the whole reporting tree) therefore lives two levels down, behind a menu
named for the thing they are not doing.

This module splits them, without touching Odoo's own code:

  Invoicing   Dashboard, Customers, Vendors -- the document flow. Reachable by
              anyone with `account.group_account_invoice`.
  Accounting  Entries, Review, Reporting, Configuration -- the ledger. Gated on
              `account.group_account_readonly` / `account.group_account_manager`.

It re-parents existing menu records by XML ID rather than redefining them, so the
custom accounting modules that hang 75 menu items off `account.menu_finance_*`
follow their parents automatically and need no change.
""",
    "author": "BCT Analytics Platform",
    "website": "https://example.invalid/bct",
    "category": "Accounting/Accounting",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    # The suite's accounting modules are dependencies because this module's whole
    # job is to arrange THEIR menus: re-parenting `custom_coretax.menu_coretax_root`
    # requires that record to exist. A deployment that wants a slimmer accounting
    # stack uninstalls this module rather than editing it.
    "depends": [
        "account",
        "custom_coretax",
        "custom_coretax_bupot",
        "custom_pph_witholding",
        "custom_tax_id",
        "custom_accounting_full",
        "custom_accounting_asset",
        "custom_accounting_recurring",
    ],
    "data": [
        "views/accounting_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
