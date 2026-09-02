# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Sale / Transaction",
    "summary": "PPOB transaction state machine, atomic wallet + bucket "
    "drawdown, provider dispatch, stale reaper, PMK-63 margin VAT.",
    "description": """
Custom PPOB Suite - Sale / Transaction
======================================
Transactional core for the PPOB vertical. Holds ``custom.ppob.transaction`` with
a state machine:

    pending -> inquiry_ok -> in_progress -> success / failed / timeout / refunded

On dispatch: atomic wallet debit, provider deposit (bucket) debit, provider
adapter call. GL is posted by the wallet + bucket atomic helpers (each posts its
own paired entry) -- there is no separate compound sale move.

Merged from era_ppob_tax: per-transaction ``vat_mode`` / ``dpp_amount`` /
``ppn_amount`` (PMK-63/2022 margin / DPP nilai lain / gross / exempt), computed
for reporting and daily rollup faktur aggregation. PPN is recognised in GL at the
rollup faktur, not per transaction.

Adds the ``ppob_transaction_id`` back-reference to wallet + bucket sub-ledgers,
a manual-sale wizard, and a cron reaper that resolves stale ``in_progress``
transactions by calling the provider adapter's ``status()`` before refunding
(never blind-refunds; honours per-provider ``stale_threshold_minutes``).
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_wallet",
        "custom_ppob_provider",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/cron_reaper.xml",
        "wizard/ppob_manual_sale_wizard_views.xml",
        "views/ppob_transaction_views.xml",
        "views/ppob_wallet_move_views.xml",
        "views/ppob_provider_bucket_move_views.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": False,
    "installable": True,
    "auto_install": False,
}
