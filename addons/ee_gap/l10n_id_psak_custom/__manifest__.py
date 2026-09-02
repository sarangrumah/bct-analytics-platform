# -*- coding: utf-8 -*-
{
    "name": "Indonesia — PSAK Chart of Accounts (Custom Platform)",
    "summary": "5-digit PSAK-aligned Chart of Accounts for Indonesian tenants, with PPN 11% taxes and fiscal positions",
    "description": """
Indonesia PSAK Chart of Accounts — Custom Platform
==================================================

Provides an alternative 5-digit Indonesian CoA aligned with the PSAK
account-number convention commonly used in Indonesian SMEs and
mid-market, distinct from the upstream `l10n_id` 4-digit template.

Structure
---------
- 1xxxx Aset / 2xxxx Kewajiban / 3xxxx Ekuitas / 4xxxx Pendapatan /
  5xxxx HPP / 6xxxx Beban Operasional / 7xxxx Pendapatan-Beban Lain /
  8xxxx Pajak Penghasilan
- 53 accounts + 12 hierarchical account groups
- 2 PPN 11% taxes (PMK 58/2022) — Keluaran / Masukan, with explicit
  repartition lines pointing to 21400 (PPN liability) / 11500 (PPN asset)
- 6 journals with Bahasa labels (Faktur Penjualan, Tagihan Pembelian,
  Kas, Bank, Jurnal Umum, Selisih Kurs)
- 2 fiscal positions (Ekspor → drops PPN, Pelanggan Bebas Pajak)

This module is registered under the 'Accounting/Localizations/Account
Charts' category so that Odoo's `ir.module.module._compute_account_templates`
discovers the @template methods and exposes the 'id_psak' chart in
Settings → Accounting → Chart Template.

PPh witholding taxes (21/23/26) are intentionally NOT included here;
they belong in the future `custom_pph_witholding` module which feeds
Bupot lines into Coretax.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Localizations/Account Charts",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "countries": ["id"],
    "data": [],
    "installable": True,
    "auto_install": True,
}
