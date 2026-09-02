# -*- coding: utf-8 -*-
{
    "name": "Custom PPh Witholding Engine",
    "summary": "Generic PPh witholding engine (rates registry + computation "
    "+ application log) for Indonesian Income Tax.",
    "description": """
Custom PPh Witholding Engine
============================

Extracts the PPh23 witholding logic that was previously embedded in
``era_ppob_commission`` and generalises it into a reusable service.

Models
------
* ``custom.witholding.rate`` — rate matrix keyed by PPh type, service
  category, and validity window. Distinguishes with-NPWP vs without-NPWP
  (Indonesian NPWP = 15 or 16 digit numeric).
* ``custom.witholding.engine`` — abstract service exposing
  ``compute(partner, amount, pph_type, date)``.
* ``custom.witholding.application`` — append-only log of every
  computed/applied withholding event.

Triggers
--------
* Manual wizard "Apply Witholding" on any ``account.move``.
* Lazy hooks on ``hr.payslip`` and ``account.payment`` (gated by
  module-installed check — no hard dependency on payroll).
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/Compliance/Coretax",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_coretax",
        "custom_hr_payroll_id",
        "account",
        "mail",
    ],
    "capability_tags": ["indonesian-tax", "withholding", "coretax", "accounting", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/witholding_security.xml",
        "security/ir.model.access.csv",
        "wizards/apply_witholding_wizard_views.xml",
        "views/witholding_rate_views.xml",
        "views/witholding_application_views.xml",
        "views/account_move_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
