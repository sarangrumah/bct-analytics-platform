# -*- coding: utf-8 -*-
{
    "name": "the tenant Document Numbering",
    "version": "19.0.1.0.0",
    "summary": "Per-company document numbering (SQ/SO/PO/INV/DO/BAST) with monthly reset for the tenant.",
    "description": """
the tenant Document Numbering
===========================
Applies the tenant's document-number format from the master-data "Document #"
sheet to the two companies in the tenant (PT the tenant, PT the tenant):

  Sales Quotation   SQ/<CO>/YYYY/MM/NNN   (sale.order while draft/sent)
  Sales Order       SO/<CO>/YYYY/MM/NNN   (sale.order, re-numbered on confirm)
  Purchase Order    PO/<CO>/YYYY/MM/NNN   (purchase.order)
  Invoice           INV/<CO>/YYYY/MM/NNN  (account.move, customer invoice)
  Delivery Order    DO/the tenant/YYYY/MM/NNN    (stock.picking, outgoing, the tenant only)
  BAST              BAST/<CO>/YYYY/MM/NNN (custom.bast.document)

<CO> is the company short code (the tenant / the tenant) held on ``res.company.x_doc_code``.
NNN is a 3-digit running number that RESETS every month.

Mechanism
---------
* Per-company ``ir.sequence`` records (``company_id`` set) so each company gets
  its own prefix; ``next_by_code`` automatically picks the active company's one.
* Monthly reset via ``use_date_range`` + a scoped ``ir.sequence`` override that
  creates MONTHLY date ranges (stock Odoo only creates yearly ranges).
* Quotation -> Sales Order re-numbering on confirm; the original SQ number is
  kept on ``sale.order.x_quotation_name`` for audit.
* Customer invoices use a monthly ``_get_starting_sequence`` (gated to companies
  that have ``x_doc_code`` set), so other tenants/companies are untouched.

TENANT-SCOPED: install only on the tenant DBs (e.g. prd_tenant,
trn_tenant). Behaviour is gated by ``res.company.x_doc_code`` so the module is
inert for any company without a code.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Tenants/the tenant",
    "depends": [
        "sale_management",
        "purchase",
        "stock",
        "account",
        "custom_bast",
        "custom_core",
    ],
    "data": [
        "views/res_company_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
