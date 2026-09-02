# -*- coding: utf-8 -*-
{
    "name": "the apparel brand Fixed Asset Revaluation Accounts",
    "version": "19.0.2.0.0",
    "summary": "Seed the 6 EBR fixed-asset categories and wire IAS 16 revaluation "
    "account defaults onto fixed-asset groups by resolving 10-digit chart codes per "
    "company.",
    "description": """
the apparel brand Fixed Asset Revaluation Accounts
=======================================
Seeds the 6 EBR fixed-asset categories (Land, Building & improvements, Vehicles,
Office & outlet equipment, Machinery, Furniture & fixtures) as
``custom.fixed.asset.group`` records — each wired to its 10-digit chart cost /
accumulated-depreciation / depreciation-expense account by code per company — and
fills the IAS 16 revaluation account defaults on every ``custom.fixed.asset.group``
(``default_revaluation_surplus_account_id`` / ``_loss_`` / ``_income_`` /
``default_retained_earnings_account_id``) for companies running the 10-digit chart.

Accounts are resolved **by code within each company** (not by XML id, which is
company-prefixed after the chart loads), so the wiring is correct across every
tenant company and survives upgrades. The setup is:

* **idempotent** — re-run on every ``-u``; only empty fields are filled, so manual
  overrides and previous runs are preserved;
* **self-scoping** — a company without the group codes (e.g. an id_psak company)
  is skipped automatically;
* **bootstrapping** — if a company has no asset group yet, a default "General"
  group is created so the revaluation defaults have somewhere to live.

the group code map:

===============================  ===========  ==================================
Role (IAS 16)                    Code         Account
===============================  ===========  ==================================
Revaluation surplus (OCI)        3005200004   OCI Current - Fixed Assets
Revaluation loss (P&L)           7706000000   Loss on impairment of Fixed Assets
Revaluation income (P&L)         8300000004   OCI - Fixed assets
Retained earnings                3006100001   Retained earnings - beginning
===============================  ===========  ==================================

TENANT-SCOPED: install only on the apparel brand / the group tenant databases.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Tenants/Retail",
    "license": "LGPL-3",
    "depends": [
        "custom_accounting_asset",
    ],
    "data": [
        "views/generated_search_views.xml",
        # Order matters: seed the 6 groups first, then wire revaluation defaults
        # onto them.
        "data/asset_group_defaults.xml",
        "data/revaluation_account_defaults.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
}
