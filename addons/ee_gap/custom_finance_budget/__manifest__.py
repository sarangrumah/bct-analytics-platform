# -*- coding: utf-8 -*-
{
    "name": "Custom Finance Budget",
    "summary": "Cost budget per division/period (synced from SAP) with consumption check for Finance Portal submissions",
    "description": """
Custom Finance Budget
=====================

Read-only cost budget reference (per division / cost-center / period) synced
from SAP by ``custom_finance_portal_sap``. Provides ``_check_document_budget``,
called by ``finance.document.mixin`` on submission to block a Finance Portal
document whose amount would exceed the remaining budget for its division.

Enforcement is soft: when no matching budget row exists (budget not yet loaded
for that division/year) the check passes, so the portal stays usable before the
SAP budget feed is live. Toggle hard blocking with the
``custom_finance_budget.enforce`` config parameter (default ``1``).
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Finance",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_finance_portal",
    ],
    "capability_tags": ["finance-portal", "budget-control"],
    "data": [
        "views/generated_search_views.xml",
        "security/ir.model.access.csv",
        "views/finance_budget_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
