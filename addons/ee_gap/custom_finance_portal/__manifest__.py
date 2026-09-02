# -*- coding: utf-8 -*-
{
    "name": "Custom Finance Portal",
    "summary": "Finance Portal engagement layer over SAP — Cash Advance, Reimbursement, Vendor Invoice, Travel settlement with Tax/Finance approval (no native GL posting)",
    "description": """
Custom Finance Portal
=====================

Odoo as a **system of engagement** in front of SAP S/4HANA (system of record).
Odoo runs the submission forms, the two-stage **Tax Review -> Finance Review**
approval, and budget/PR validation; the approved document is later pushed to SAP
(by ``custom_finance_portal_sap``) which posts the GL / MIRO and pays. Odoo never
posts its own journal entries — it only mirrors SAP status back.

Document types (all use ``approval.mixin`` + ``pdp.audited.mixin``):

- ``finance.cash.advance`` + ``finance.cash.advance.realization``
- ``finance.reimbursement`` (Reimbursement & Expenses)
- ``finance.vendor.invoice`` (PO Non-Trade / Non-PO Non-Trade) + vendor portal
- ``finance.travel.settlement`` (read-only mirror of HRIS travel, settled vs CA)

Light master data: submission type, invoice type/routine, item of submission,
vertical (division), limitation for submission (incl. the PR-required threshold).

The SAP push and status mirror are *hooks* (``_finance_push_to_sap`` /
``_finance_apply_sap_status``) overridden by ``custom_finance_portal_sap``; out of
the box they run in a local stub so the engagement layer is usable standalone.

Part of the Custom Platform — multi-tenant Odoo 19 for Indonesian SMB.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Finance",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_approval_engine",
        "mail",
        "portal",
        "hr",
        "account",
        "product",
    ],
    "capability_tags": [
        "finance-portal",
        "approval-workflow",
        "sap-engagement",
        "audit-trail",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/finance_master_views.xml",
        "views/finance_cash_advance_views.xml",
        "views/finance_reimbursement_views.xml",
        "views/finance_vendor_invoice_views.xml",
        "views/finance_travel_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
