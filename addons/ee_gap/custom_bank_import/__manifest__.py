# -*- coding: utf-8 -*-
{
    "name": "Custom Bank Import",
    "summary": "CSV template-based bank statement import + H2H bank API adapter framework",
    "description": """
Custom Bank Import
==================

Two complementary import pipelines for Indonesian banks (BCA, Mandiri,
BNI, BRI, CIMB, Permata, Danamon, plus a generic HTTP fallback):

1. **CSV / XLSX template-based import** — declare per-bank parsing rules
   (column indexes, date format, encoding, signed vs split debit/credit)
   in ``custom.bank.import.template`` records, then upload statements via
   wizard. Lines are written to ``account.bank.statement.line`` against
   the chosen bank journal.

2. **Host-to-host (H2H) API sync** — built on top of
   ``custom_adapter_framework`` (HMAC signing, retry, circuit breaker).
   Each bank ships a registered adapter (``bank_bca_h2h``, etc.) that
   implements ``inquiry_balance`` and ``inquiry_statement``. A scheduler
   pulls statements on the configured interval.

All imports are tracked in ``custom.bank.import.log`` with file hash
deduplication and PDP audit-log integration.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Bank",
    "version": "19.0.0.3.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_adapter_framework",
        "account",
    ],
    "capability_tags": ["bank-import", "accounting", "audit-trail", "h2h-api"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/bank_import_template_views.xml",
        "views/bank_import_log_views.xml",
        "views/bank_h2h_connection_views.xml",
        "wizard/bank_import_csv_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
