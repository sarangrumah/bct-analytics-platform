# -*- coding: utf-8 -*-
{
    "name": "the tenant Show Date",
    "version": "19.0.1.5.0",
    "summary": "Show-date, event and DP fields on quotation/SO/customer invoice, "
    "with payment terms anchored to the show date. PT the tenant only.",
    "description": """
the tenant Show Date
==============
Adds a 'Show Date' (``x_custom_show_date``) Date field captured on the
Quotation (``sale.order``), carried to the Sales Order, and propagated to the
Customer Invoice (``account.move``, ``out_invoice``). For companies that opt in
via ``res.company.x_custom_show_date_enabled``, the customer-invoice
payment-term due dates (``date_maturity``) are anchored to the Show Date instead
of the invoice date (i.e. "X days after show date").

Event description block
-----------------------
The same opt-in also captures ``x_custom_event_name``,
``x_custom_event_location`` and ``x_custom_dp_note`` on the order, and appends
them (with the show date, ``dd.mm.yy``) as a second line under every product
line's description::

    Jasa Drone Show 250 unit
    Event Danone, Lokasi Taman Bhagawan Bali, 07.08.26, DP 50%

The description is a stored compute with ``readonly=False``, so the block is
rebuilt whenever the event data changes but manual edits survive in between. It
travels to the customer invoice through the standard ``_prepare_invoice_line``.
The down-payment line carries the same event data in its own one-line form
(see below).

Down-payment line description
-----------------------------
Core labels the down-payment invoice line ``Down payment of 50.00%``, and that
wording is what the customer sees on the invoice PDF *and* in the Faktur Pajak
"Nama Barang Jasa" cell — ``custom_coretax_export`` reads
``line.product_id.name or line.name``, and a DP line carries no product, so it
falls through to the name. For flagged companies the line is relabelled with the
products being down-paid followed by the event detail instead::

    Jasa Drone Show 250 Unit, Biaya Luar Kota, Event Danone,
    Lokasi Taman Bhagawan Bali, 07.08.26 (Uang Muka 50%)

Rewriting the stored ``name`` fixes both printouts from one place;
``custom_report_templates`` and ``custom_coretax_export`` are shared addons and
stay untouched. The string is deliberately kept to a single line because it
lands in one cell of the coretax import file. A fixed-amount down payment gets
``(Uang Muka)`` with no percentage, and ``x_custom_dp_note`` is left out of this
line because the trailing marker already states the down payment. The journal item carries the same label; the
down payment stays identifiable in the GL through account 2108100001 and the
order's "Down Payments" section.

TENANT-SCOPED: built for the PT the tenant company on the tenant DBs
(uat_tenant, rnd_tenant, prd_EAL_Tenant). The behaviour is gated by the
``res.company`` boolean flag, NOT by company name and NOT merely by install, so
the module is safe to install on a multi-company DB (e.g. the tenant + the tenant): only the
company with the flag ticked (PT the tenant) is affected. Until the flag is ticked the
module is inert.
""",
    "author": "Platform",
    "website": "https://example.com/custom-platform",
    "category": "Tenants/the tenant",
    "depends": ["sale_management", "account", "custom_core", "custom_accounting_reports"],
    "data": [
        "views/res_company_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/profit_loss_wizard_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
    "license": "LGPL-3",
}
