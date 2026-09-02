# -*- coding: utf-8 -*-
{
    "name": "Indonesia - 10-Digit Chart of Accounts",
    "summary": "10-digit Indonesian CoA + PPN/PPh taxes, journals, fiscal positions",
    "description": """
10-digit chart localization (selectable chart template, code ``id_coa_10d``).

Provides a ready-to-use Indonesian accounting package for any new company: the generic 10-digit chart of accounts (bank/cash and entity-named
accounts excluded — Odoo auto-creates generic bank/cash accounts from the code
prefixes), the full PPN/PPh tax set with tax groups, sale/purchase/general
journals, fiscal positions, and company accounting defaults.

Data is generated from the master COA (imports/tenant_coa.csv) and the live
Tenant tax configuration via tools/gen_l10n_id_coa_10d.py.
""",
    "author": "Custom Platform",
    "category": "Accounting/Localizations/Account Charts",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "countries": ["id"],
    "data": [],
    "installable": True,
    "auto_install": False,
}
