# -*- coding: utf-8 -*-
{
    "name": "Custom Accounting - Fixed Assets",
    "summary": "Fixed asset register with depreciation schedule, monthly posting cron, and disposal workflow",
    "description": """
Custom Accounting Fixed Assets
==============================
Closes the EE 'account_asset' gap for Odoo CE. Provides:

* ``custom.fixed.asset`` — acquisition value, salvage, useful life, straight-line method,
  state machine (draft / running / disposed / cancelled), mail tracking and PDP audit.
* ``custom.fixed.asset.group`` — categorisation with default useful life and default
  asset / accumulated depreciation / depreciation expense accounts.
* ``custom.fixed.asset.location`` — hierarchical physical location tree.
* ``custom.fixed.asset.depreciation.line`` — generated monthly schedule with posted
  flag and link to its ``account.move``.
* Monthly cron auto-posts all due (date <= today, not yet posted) depreciation lines
  for running assets, debiting depreciation expense and crediting accumulated
  depreciation per the asset/group account configuration.
* Disposal wizard captures disposal date and sale value, computes gain/loss vs. NBV,
  and writes the asset back to ``disposed`` state with full audit trail.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.0.5.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_accounting_full",
        "custom_accounting_reports",
        "account",
    ],
    "capability_tags": ["accounting", "fixed-assets", "depreciation", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "data/server_actions.xml",
        "views/fixed_asset_group_views.xml",
        "views/fixed_asset_location_views.xml",
        "views/fixed_asset_views.xml",
        "views/fixed_asset_revaluation_views.xml",
        "wizards/asset_disposal_wizard_views.xml",
        "wizards/asset_register_wizard_views.xml",
        "wizards/asset_post_wizard_views.xml",
        "wizards/asset_revaluation_wizard_views.xml",
        "report/asset_register_report.xml",
        "report/asset_register_template.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
