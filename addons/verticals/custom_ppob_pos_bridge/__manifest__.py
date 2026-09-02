# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - POS Bridge (Mirror)",
    "summary": "Mirror POS POS + H2H feeds into Odoo Finance/Accounting "
    "(2-feed HMAC ingest, join by pos_trx_ref, GL-on-terminal).",
    "description": """
Custom PPOB Suite - POS Bridge (Revamp I: mirror-only)
===========================================================
Revamp I of the POS PPOB architecture: POS POS (standalone) and the
H2H switcher own the transaction; Odoo is a **downstream, non-authoritative
ledger** that mirrors two terminal feeds and projects them into GL.

Generalises ``custom_ppob_oracle_bridge`` from Oracle stored-procedure polling
to **HTTP push ingest** with two independent, idempotent feeds joined by a
shared correlation key (``pos_trx_ref``):

* ``POST /api/ppob/pos/pos``  -- sales + top-up + refund + mitra balance.
* ``POST /api/ppob/pos/h2h``  -- biller fulfillment + cost + deposit.

Both feeds are authenticated with HMAC-SHA256 over ``timestamp || body`` (per-
feed secret via ``credential_ref`` -> ir.config_parameter), IP allowlist and
Redis-backed nonce replay guard (reused from ``custom_core``). Per-feed
idempotency is a DB ``UNIQUE`` on the feed external ref.

* ``custom.ppob.pos.txn``      -- join/correlation model (match_state
  pos_only/h2h_only/matched/mismatch), computed margin.
* ``custom.ppob.pos.connection`` -- per-feed HMAC credential + IP allowlist.
* ``custom.ppob.pos.settlement`` -- EOD/settlement + balance snapshots.
* ``custom.ppob.pos.ingest.skipped`` -- replay queue for unmapped events.
* Wallet ``pos_mirror`` flag + ``_mirror_debit``/``_mirror_credit`` (post
  GL Dr Wallet / Cr Revenue on the POS feed WITHOUT the native credit ceiling --
  out-of-order feeds must never block posting).
* Reconciliation cron: flag stale ``pos_only`` + balance-snapshot variance.

Additive and independently uninstallable; NOT in the default industry pack
(enable per tenant running the POS + H2H channel). This module does NOT
call billers, hold mitra balance, or execute purchases -- see
``docs/projects/ppob/ppob-pos-h2h-architecture.md``.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
        "custom_ppob_wallet",
        "custom_ppob_va",
        "custom_ppob_provider",
        "custom_core",
    ],
    "external_dependencies": {
        "python": [],
    },
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/cron_pos.xml",
        "wizards/pos_backfill_wizard_views.xml",
        "views/pos_connection_views.xml",
        "views/pos_txn_views.xml",
        "views/pos_settlement_views.xml",
        "views/pos_ingest_skipped_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
