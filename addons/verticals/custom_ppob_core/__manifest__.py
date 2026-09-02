# -*- coding: utf-8 -*-
{
    "name": "Custom PPOB - Core",
    "summary": "Foundation for the PPOB vertical: mitra/provider partners, "
    "product catalogue, pricing tiers, chart-of-account scaffolding.",
    "description": """
Custom PPOB Suite - Core
========================
Foundation module for the PPOB (Payment Point Online Bank) business layer on
the Custom Platform. Ported from the ERA PPOB R&D suite (era_ppob_core) and
rewired onto platform accounting/tax modules.

Provides:

* Partner extensions marking mitra (B2B reseller) and provider (supplier) with
  per-partner transaction caps and an NPWP flag (drives PPh withholding rate).
* Product classification (``custom.ppob.product.class``) driving wallet routing
  and VAT mode per class (margin / other valuation / gross / exempt) per
  PMK-63/2022 for pulsa/voucher distributors.
* PPOB product catalogue (pulsa, token, bill) with denomination, default cost
  price, inquiry-required flag, and GL account overrides.
* Mitra pricing tiers with per-product selling prices.
* Role-addressed chart-of-account scaffolding created idempotently through a
  post-init hook (search-by-code-then-create) so it slots beside a tenant's
  existing l10n_id / PSAK chart without duplicate accounts.
* Sequences for transactions, wallet moves, bucket moves, VA topups, accruals.
* Security groups (user / operations / manager / API integration).
""",
    "author": "Custom Platform Team",
    "website": "https://custom.local",
    "category": "Industry/PPOB",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "depends": [
        "account",
        "product",
        "custom_core",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/ppob_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/ppob_product_class_views.xml",
        "views/ppob_product_views.xml",
        "views/ppob_price_tier_views.xml",
        "views/ppob_account_mapping_views.xml",
        "views/res_partner_views.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
    "auto_install": False,
}
