# -*- coding: utf-8 -*-
{
    "name": "Custom HR Payroll (Indonesia)",
    "summary": "PPh 21 TER + Annual progressive, BPJS Kes/TK, PTKP, THR, SPT 1721 A1",
    "description": """
Custom HR Payroll (Indonesia)
=============================

Indonesian payroll engine — self-contained, does not require Odoo EE
``hr_payroll``. Stays current with PP 58/2023 (TER) and UU HPP 2021.

- TER (PP 58/2023) monthly withholding per Kategori A/B/C.
- Annual progressive (UU HPP) for December reconciliation.
- BPJS Kesehatan 1%/4% capped Rp 12M; JHT 2%/3.7%; JKK 0.24-1.74%;
  JKM 0.3%; JP 1%/2% capped Rp 10,042,300.
- Coretax Bupot PPh 21 link on payslip approve.
- SPT 1721 A1 generator (PDF + XML).
- Audit chain to pdp.audit_log.
""",
    "author": "Custom Platform",
    "category": "Human Resources/Payroll",
    "version": "19.0.0.1.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_pdp_taxonomy",
        "custom_coretax",
        "hr",
        "mail",
    ],
    "capability_tags": ["indonesian-payroll", "payroll", "indonesian-tax", "withholding", "coretax", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/custom_hr_payroll_id_security.xml",
        "security/ir.model.access.csv",
        "data/hr_payroll_config_data.xml",
        "data/hr_payroll_ter_data.xml",
        "views/hr_payroll_config_views.xml",
        "views/hr_payroll_ter_views.xml",
        "views/hr_employee_views.xml",
        "views/hr_payslip_views.xml",
        "wizards/hr_payslip_batch_wizard_views.xml",
        "wizards/hr_payroll_spt_a1_wizard_views.xml",
        "reports/spt_1721_a1_template.xml",
        "views/menu_views.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
