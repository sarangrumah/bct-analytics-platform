# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Virtual Account",
    "summary": "Mitra virtual accounts + wallet top-up (bank H2H callbacks + "
    "CSV reconcile), idempotent by bank reference.",
    "description": """
Custom PPOB Suite - Virtual Account
===================================
Top-up pipeline for mitra wallets via bank Virtual Account (BCA/BNI/BRI/Mandiri/
Permata):

* Host-to-Host: ``/api/ppob/va/<bank>/inquiry`` and ``/payment`` endpoints,
  authenticated per bank with HMAC-SHA256 (timestamp + body) using the platform
  secure-endpoint primitives -- Redis-backed nonce replay guard + IP allowlist +
  clock-skew check. The hard idempotency guarantee is the DB
  ``UNIQUE(bank_ref)`` on ``custom.ppob.va.topup``: a duplicate callback credits
  the wallet exactly once and returns the original acknowledgement.
* Manual / reconcile: a ``va_match`` extension of ``custom.reconcile.rule``
  matches bank-statement references against ``custom.ppob.va.account`` records
  and credits the correct wallet (reuses custom_bank_import / custom_accounting_full).

Optional per-VA output tax splits each top-up into DPP (wallet credit) + PPN
(Output VAT) via the wallet's tax-inclusive credit primitive.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_wallet",
        "custom_core",
        "custom_accounting_full",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "views/ppob_va_account_views.xml",
        "views/ppob_va_topup_views.xml",
        "views/ppob_va_bank_connection_views.xml",
        "views/reconcile_rule_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
