# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - PPS Gateway (H2H inbound)",
    "summary": "Expose the PPS/EVShop H2H API from Odoo so POS POS can "
    "transact against Odoo as the switcher (Revamp II).",
    "description": """
Custom PPOB Suite - PPS Gateway (Revamp II: Odoo as switcher)
=============================================================
Revamp II makes Odoo REPLACE the vendor PPS/EVShop switcher. POS POS keeps
its existing integration and simply re-points its base URL to Odoo: this module
exposes the SAME PPS H2H API surface as a drop-in and maps every request onto
the native ``custom.ppob.transaction`` engine + wallet ("deposit") + provider
adapter registry. Odoo fulfils to real billers itself (its own adapters); the
vendor PPS is NOT called downstream.

Endpoints (mimicking the PPS contract, MD5-signed per the vendor spec):

  * POST /pps/sell                  -> create + dispatch a transaction (RC 9/0/1)
  * POST /pps/statustrx             -> latest status of a Sell
  * POST /pps/statustrxwithdeposit  -> + the mitra deposit (wallet balance)
  * POST /pps/checknocustomer       -> e-wallet name inquiry (adapter.inquiry)
  * POST /pps/inquiry-pln (JSON)    -> PLN inquiry (meter/name/tariff)
  * POST /pps/game-list   (JSON)    -> catalog of game products + dynamic fields
  * POST /pps/direct-topup(JSON)    -> game top-up with dynamic field payload

Async: Sell returns pending, the engine drives to terminal, and a cron fires the
GET Callback to the POS callback URL within the SLA; StatusTrx is the fallback.

Security note: the PPS contract mandates **MD5** signatures (per-endpoint
formulas). MD5 is cryptographically weak; it is confined to
``controllers/pps_signature.py`` and compensated by IP allowlist + replay guard

+ timestamp/notrx freshness. The platform HMAC-SHA256 convention is NOT diluted

-- nothing outside this module imports ``pps_signature``.

Real biller integration is a separate concern: each biller is a
``@register_adapter`` subclass wired to a ``custom.ppob.provider``. This gateway
never references a concrete biller -- it only calls ``provider._get_adapter()``
-- so it is fully testable against the existing ``ppob_mock`` adapter.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_sale",
        "custom_ppob_provider",
        "custom_ppob_core",
        "custom_core",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_serveridtrx.xml",
        "data/cron_callback_dispatch.xml",
        "views/pps_mitra_credential_views.xml",
        "views/pps_callback_log_views.xml",
        "views/pps_game_field_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
