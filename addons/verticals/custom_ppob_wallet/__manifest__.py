# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Wallet",
    "summary": "Mitra prepaid wallets with atomic, row-locked debit/credit primitives and a paired GL ledger.",
    "description": """
Custom PPOB Suite - Wallet
==========================
One wallet per (mitra, product class). Exposes the atomic money primitives the
rest of the PPOB suite builds on:

* ``_atomic_debit`` / ``_atomic_credit`` -- row-level ``SELECT ... FOR UPDATE``
  lock, paired ``account.move`` posting, ``custom.ppob.wallet.move`` sub-ledger
  line, and balance update, all inside one PostgreSQL transaction.
* ``_atomic_credit_with_tax`` -- tax-inclusive credit that splits gross into DPP
  (grows the wallet) and PPN (routed to the output-tax repartition account).
* Manual adjust / topup wizard.
* Dedicated wallet + sale general journals.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/account_journal_data.xml",
        "wizard/ppob_wallet_adjust_wizard_views.xml",
        "views/ppob_wallet_views.xml",
        "views/ppob_wallet_move_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
