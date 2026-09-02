# -*- coding: utf-8 -*-
{
    "name": "Custom Expenses",
    "summary": "AI OCR receipt extraction, generic approval engine, PDP audit, expense reports, corporate cards, mileage tracking, reimbursement payments for hr.expense",
    "description": """
Custom Expenses extends the standard ``hr_expense`` application with:

- AI-assisted receipt OCR extraction (amount, date, vendor, tax_amount,
  currency_code, confidence) delivered via ``custom_ai_bridge``
  (``custom.ai._recommend``) sending the receipt attachment as base64.
- Generic approval workflow plug-in via ``custom_approval_engine``'s
  ``approval.mixin`` (replacing the standard manager-only submit chain).
- ``custom.expense.report`` — bulk submit / approve / register payment for
  a batch of expenses sharing the same employee.
- ``custom.expense.corporate.card`` — masked card number + bank journal
  linkage so expenses paid via corporate card auto-mark payment_mode as
  ``company_account`` and skip the reimbursement queue.
- Mileage tracking — per-km rate driven by ``ir.config_parameter``
  ``custom_expenses.id_mileage_rate`` (default 5000 IDR/km).
- Reimbursement payment flow — generate ``account.payment`` for own-account
  expenses approved through the matrix.
- PDP audit logging on submit / approve transitions through the
  ``custom_pdp_audit`` infrastructure.

Part of the Custom Platform — multi-tenant Odoo 19 for Indonesian SMB.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Expenses",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "custom_approval_engine",
        "hr_expense",
        "account",
        "product",
        "mail",
    ],
    "capability_tags": ["expense-management", "ai", "approval-workflow", "audit-trail", "ocr"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/expense_config.xml",
        "data/expense_mileage_product.xml",
        "views/hr_expense_views.xml",
        "views/custom_expense_corporate_card_views.xml",
        "views/custom_expense_report_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
