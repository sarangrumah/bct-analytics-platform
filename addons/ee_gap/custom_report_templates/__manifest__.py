# -*- coding: utf-8 -*-
{
    "name": "Custom Report Templates",
    "summary": "Branded Invoice / Sales Quotation / Purchase Order PDF layouts with per-tenant branding",
    "description": """
Re-styles the three core business documents — Customer Invoice, Sales
Quotation/Order and Purchase Order — to a clean Wave/Excel-style layout
(branded header, FOR/BILL-TO band, items table with a TAXED column,
OTHER COMMENTS box, Subtotal/VAT/TOTAL/BALANCE DUE block, signature area).

Layout is shared across all tenants; branding (logo, accent colour, bank
details, NPWP, address) is read from `res.company`, so each tenant prints
its own look with no per-tenant code fork.

This is the branded baseline; `custom_studio_lite` remains available for
ad-hoc header/footer/XPath tweaks on top.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Custom Platform/EE Gap",
    "version": "19.0.0.5.0",
    "license": "LGPL-3",
    "depends": ["account", "sale", "purchase", "custom_core", "custom_home_console"],
    "capability_tags": ["reporting", "branding"],
    "data": [
        "reports/paperformat.xml",
        "reports/report_common_templates.xml",
        "reports/report_invoice.xml",
        "reports/report_saleorder.xml",
        "reports/report_purchaseorder.xml",
        "reports/report_journal_voucher.xml",
        "reports/report_actions.xml",
        "views/res_company_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
