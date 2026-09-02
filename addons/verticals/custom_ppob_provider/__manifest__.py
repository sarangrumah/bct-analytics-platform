# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Provider",
    "summary": "Provider master data, atomic bucket inventory, SKU mapping, "
    "adapter abstraction, DP 100% deposit topup.",
    "description": """
Custom PPOB Suite - Provider
============================
Provider-side management for the PPOB vertical:

* ``custom.ppob.provider`` master with settlement mode (prepaid deposit /
  postpaid), bucket mode (fixed_denom / bulky), tax-on-topup config, adapter
  class, and per-tenant HTTP credentials via a linked ``custom.adapter.config``.
* ``custom.ppob.provider.bucket`` -- running IDR ex-tax inventory per provider
  with row-locked atomic credit/debit and paired GL posting (Input VAT split).
* ``custom.ppob.provider.bucket.move`` sub-ledger.
* ``custom.ppob.provider.sku.map`` internal SKU -> provider SKU + buy price +
  failover priority.
* Domain-verb adapter registry (``inquiry`` / ``pay`` / ``status`` / ``topup``)
  with ``ppob_mock`` and ``ppob_http_json`` implementations. HTTP transport is a
  single signed POST per call -- pay is never retried, so it cannot double-sell.
* DP 100% + Pelunasan vendor-bill topup wizard (PMK 131/2024 Coretax split),
  idempotent via ``custom.ppob.provider.topup.log``.
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "custom_ppob_core",
        "custom_adapter_framework",
        "stock",
        "purchase",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "data/account_journal_data.xml",
        "data/dp_product_data.xml",
        "wizard/ppob_provider_topup_wizard_views.xml",
        "views/ppob_provider_views.xml",
        "views/ppob_provider_bucket_views.xml",
        "views/ppob_provider_sku_map_views.xml",
        "views/account_move_views.xml",
        "views/ppob_provider_topup_log_views.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": False,
    "installable": True,
    "auto_install": False,
}
