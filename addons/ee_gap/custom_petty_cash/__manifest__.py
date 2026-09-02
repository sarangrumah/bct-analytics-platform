# -*- coding: utf-8 -*-
{
    "name": "Custom Petty Cash",
    "summary": "Employee petty cash advances: request, Finance approval, Bank-Out "
    "disbursement, realization (third-party vendor bill or plain expense), "
    "return/top-up settlement, plus outstanding & aging monitoring.",
    "description": """
Custom Petty Cash — Uang Muka Operasional
==========================================

A full petty-cash cycle for Finance and employees on Odoo 19 Community:

1. **Pengajuan** — an employee requests petty cash for an Operating Unit
   (``petty.cash.request``), optionally broken down into estimate lines.
2. **Review & Approval** — Finance reviews; approval routes through the
   generic ``custom_approval_engine`` matrix (multi-tier, delegation, SLA).
3. **Pencairan (Bank Out)** — Finance disburses the approved amount, booking
   *Dr Uang Muka Petty Cash (employee) / Cr Bank* through the Bank-Out journal.
4. **Realisasi** — the employee accounts for the spend
   (``petty.cash.realization``):

     * *Pihak ketiga* lines create a full vendor bill (billing → PPN/PPh via
       ``custom_tax_id`` → invoicing → payment against the advance → COA),
       and require the supplier invoice attachment.
     * *Expense* lines post a direct entry *Dr Expense / Cr Uang Muka*.
5. **Pengembalian / Top-up** — leftover cash is returned to the bank, or a
   shortfall is topped up, until the employee advance nets to zero and the
   request is settled (advance lines auto-reconciled).
6. **Monitoring** — kanban/list dashboards by state, a per-employee
   **Outstanding** ledger and an **Aging** report, both reusing the
   ``custom_accounting_reports`` engine (PDF / XLSX / on-screen table).

Operating-Unit aware: when the apparel brand localization is installed the OU

analytic is stamped onto every generated journal item; otherwise the module
runs unchanged.

Part of the Custom Platform — multi-tenant Odoo 19 for Indonesian SMB.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Accounting",
    "version": "19.0.0.4.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "account",
        "hr",
        "mail",
        "custom_approval_engine",
        "custom_tax_id",
        "custom_accounting_reports",
    ],
    "capability_tags": [
        "petty-cash",
        "cash-advance",
        "approval-workflow",
        "audit-trail",
        "accounting",
    ],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/record_rules.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        "views/petty_cash_request_views.xml",
        "views/petty_cash_realization_views.xml",
        "views/report_views.xml",
        "views/res_config_settings_views.xml",
        "reports/petty_cash_templates.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
