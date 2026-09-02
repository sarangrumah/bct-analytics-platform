# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Oracle Bridge",
    "summary": "Bridge the PPOB suite to the legacy Oracle EVShop pipeline "
    "(MSG016T) via stored procedure SellWithDenom_HA + status polling.",
    "description": """
Custom PPOB Suite - Oracle Bridge
=================================
Lets the PPOB vertical run in parallel with the legacy Oracle EVShop pipeline as
a second routing mode alongside native H2H. Additive and independently
uninstallable; NOT part of the default industry pack (enable per tenant that
still runs EVShop).

* ``custom.ppob.oracle.connection`` -- Oracle DSN/pool + SP service-account config.
* ``custom.ppob.oracle.member.map`` -- res.partner (mitra) <-> MSG019T.ID.
* ``custom.ppob.oracle.ingest.skipped`` -- audit for un-mappable MSG016T rows.
* ``ppob_oracle_bridge`` adapter -- pay() calls SP SellWithDenom_HA, status()
  polls MSG016T.STATUS_USSD_2_PROVIDER. Registered in the PPOB adapter registry.
* Per-provider ``bridge_mode`` (h2h_direct / oracle_bridge).
* 3 crons: status sync (1 min), inbound ingest (1 min, high-watermark cursor),
  balance mirror (5 min, MSG019T.DEPOSIT_BALANCE -> mirror wallet).
* Wallet guard: ``mirror_source='oracle'`` blocks native debit/credit (only the
  mirror cron writes via move_type='oracle_sync').
* Backfill wizard: re-ingest an MSG016T range for recovery / onboarding.

Requires the ``oracledb`` Python library (thin mode). Deploy note: the two
1-minute crons run per tenant DB -- keep this module out of tenants that do not
use EVShop.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_provider",
        "custom_ppob_sale",
        "custom_ppob_wallet",
    ],
    "external_dependencies": {
        "python": ["oracledb"],
    },
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/cron_oracle.xml",
        "wizards/oracle_backfill_wizard_views.xml",
        "views/oracle_connection_views.xml",
        "views/oracle_member_map_views.xml",
        "views/oracle_ingest_skipped_views.xml",
        "views/ppob_provider_views.xml",
        "views/ppob_transaction_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
