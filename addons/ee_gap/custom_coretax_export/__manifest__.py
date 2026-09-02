# -*- coding: utf-8 -*-
{
    "name": "Coretax Import File Export (DJP Templates)",
    "summary": "Emit DJP-conformant Coretax import workbooks: e-Faktur Keluaran (FK/OF), "
    "Retur Masukan, Bupot Unifikasi, Bupot PPh 21, Bupot Non-Resident",
    "description": """
Coretax Import File Export
==========================

Produces the XLSX workbooks DJP's Coretax accepts as *import* files. Unlike
``custom_coretax``'s XML wizard — whose envelope is a placeholder schema never
aligned to a published XSD — every layout here is transcribed column-for-column
from the official DJP template workbooks, including their typos (``Nomor
Setifikat Insentif``) and their per-template casing (``PPH23`` in Unifikasi vs
``PPh26`` in Non-Resident).

Why a standalone renderer
-------------------------
``custom.report.engine`` prefixes a title/company/period banner and formats
numbers for humans. A DJP import file must start its header on row 1 and carry
raw values, so these wizards drive ``xlsxwriter`` directly rather than
subclassing the report engine.

Templates covered
-----------------
- **e-Faktur Keluaran** (``Import FK`` sheet) — two-record layout: one ``FK``
  row per invoice followed by its ``OF`` item rows, 35 + 16 columns.
- **Retur Masukan** — three sections: NPWP Pembeli banner, ``Retur`` table
  terminated by an ``END`` sentinel, then ``DetailRetur``.
- **Bupot Unifikasi** (PPh 23 / 4(2) / 22 / 15) — 23 columns.
- **Bupot PPh 21** — 27 columns, carries Gross Up and PTKP.
- **Bupot Non-Resident** (PPh 26 / 4(2)) — 32 columns, carries TIN, kode
  negara, passport, KITAS and norma penghasilan neto.

Jenis Barang Jasa
-----------------
The FK ``OF`` rows classify each item as ``Jasa`` or ``Barang``. A down-payment
line carries no product of its own — core builds it from a "fake" SO line — so
classifying on ``line.product_id`` alone reports every down payment as
``Barang``, even one paid against a pure services order. ``_item_jenis()``
therefore falls back to the products of the originating sales order, and answers
``Jasa`` only when every product billed is a service. Ordinary lines, which do
carry a product, are unaffected. The fallback is guarded on ``sale_line_ids``
being present, so the module still works where ``sale`` is not installed.

Identity prerequisites
----------------------
The pemotong columns are read from ``res.company`` (NPWP via its partner,
``x_custom_nitku_suffix``, ``x_custom_npwp_penandatangan``,
``x_custom_coretax_user_id``); the counterparty columns from ``res.partner``
(``x_custom_npwp``, ``x_custom_nitku``, ``x_custom_tin``, …). Both are added by
``custom_tax_id``. Each wizard refuses to render when the pemotong identity is
incomplete rather than emitting a file DJP will bounce — but only for the fields
that layout actually carries: ``NPWP Penandatangan`` is a bupot column, so
e-Faktur Keluaran and Retur Masukan are not blocked on it.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Localizations",
    "version": "19.0.1.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_tax_id",
        "custom_coretax",
        "account",
    ],
    "capability_tags": ["indonesian-tax", "coretax", "efaktur", "bupot", "export"],
    "data": [
        "security/ir.model.access.csv",
        "wizards/coretax_template_export_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
