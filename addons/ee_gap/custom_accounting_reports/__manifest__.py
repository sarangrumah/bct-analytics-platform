# -*- coding: utf-8 -*-
{
    "name": "Custom Accounting Reports",
    "summary": "Production-grade financial reports engine (P&L, BS, GL, "
    "TB, Cash Flow, Aging, Partner Ledger, Tax, Day/Cash/Bank "
    "Book, Journal Audit) for the Custom Platform.",
    "description": """
Custom Accounting Reports
=========================

Closes the EE-gap on Odoo CE ``account_reports``. Provides 14 dynamic
report types built on a single shared ``custom.report.engine``
AbstractModel:

* Profit & Loss, Balance Sheet, Cash Flow (indirect method).
* General Ledger, Trial Balance, Partner Ledger.
* Aged Receivable, Aged Payable.
* Tax Report (PPN / PPh subtotals; cross-references Coretax).
* Day Book, Cash Book, Bank Book, Journal Audit.
* Tree-driven custom Financial Report (``custom.report.financial``).

All reports use parameterised SQL through Odoo's ORM helpers and
render to QWeb PDF or HTML. PSAK-aligned default tree shipped under
``data/financial_report_seed_psak.xml``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.0.15.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_accounting_full",
        "account",
    ],
    "capability_tags": ["accounting", "financial-reports", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/financial_report_seed_psak.xml",
        "views/custom_report_financial_views.xml",
        "wizard/general_ledger_wizard_views.xml",
        "wizard/trial_balance_wizard_views.xml",
        "wizard/balance_sheet_wizard_views.xml",
        "wizard/profit_loss_wizard_views.xml",
        "wizard/cash_flow_wizard_views.xml",
        "wizard/aged_receivable_wizard_views.xml",
        "wizard/aged_payable_wizard_views.xml",
        "wizard/partner_ledger_wizard_views.xml",
        "wizard/purchase_wizard_views.xml",
        "wizard/credit_limit_wizard_views.xml",
        "wizard/open_items_wizards_views.xml",
        "wizard/sales_detail_wizard_views.xml",
        "wizard/partner_card_wizard_views.xml",
        "wizard/advance_wizard_views.xml",
        "wizard/sales_wizard_views.xml",
        "wizard/tax_report_wizard_views.xml",
        "wizard/faktur_pajak_wizard_views.xml",
        "wizard/ppn_masukan_import_wizard_views.xml",
        "wizard/bupot_wizard_views.xml",
        "wizard/p2_wizards_views.xml",
        "wizard/p3p4_wizards_views.xml",
        "wizard/deferred_wizards_views.xml",
        "wizard/day_book_wizard_views.xml",
        "reports/report_actions.xml",
        "reports/purchase_template.xml",
        "reports/credit_limit_template.xml",
        "reports/open_items_templates.xml",
        "reports/sales_detail_template.xml",
        "reports/general_ledger_template.xml",
        "reports/trial_balance_template.xml",
        "reports/balance_sheet_template.xml",
        "reports/profit_loss_template.xml",
        "reports/cash_flow_template.xml",
        "reports/aged_receivable_template.xml",
        "reports/aged_payable_template.xml",
        "reports/partner_ledger_template.xml",
        "reports/partner_card_template.xml",
        "reports/advance_template.xml",
        "reports/sales_template.xml",
        "reports/tax_report_template.xml",
        "reports/faktur_pajak_template.xml",
        "reports/bupot_template.xml",
        "reports/p2_templates.xml",
        "reports/p3p4_templates.xml",
        "reports/deferred_templates.xml",
        "reports/day_book_template.xml",
        "reports/cash_book_template.xml",
        "reports/bank_book_template.xml",
        "reports/journal_audit_template.xml",
        "reports/financial_report_template.xml",
        "reports/report_common.xml",
        "views/analysis_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_accounting_reports/static/src/report_table/report_table.js",
            "custom_accounting_reports/static/src/report_table/report_table.xml",
            "custom_accounting_reports/static/src/report_table/report_table.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
