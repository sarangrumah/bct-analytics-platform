# -*- coding: utf-8 -*-
{
    "name": "Custom Tax Indonesia (PPh + DPP Nilai Lain)",
    "summary": "PPh 23 / 4(2) / 26 withholding engine, PPN DPP Nilai Lain (PMK 131/2024), Faktur Pengganti workflow",
    "description": """
Custom Tax Indonesia
====================

Closes the Indonesian withholding-tax + PPN DPP gaps left by Odoo CE
and ``custom_accounting_full``. Sits between ``account`` and
``custom_coretax`` — generates Bukti Potong drafts that Coretax then
serialises to XML.

PPh Withholding (Vendor Side)
-----------------------------
- ``tax.withholding.rule`` declares jenis penghasilan (Sewa, Jasa,
  Dividen, Bunga, Royalti, Hadiah, Imbalan jasa LN, …), tarif, NPWP
  requirement, and the hutang-pajak account to credit.
- Auto-detection on ``account.move._post`` for vendor bills (``in_invoice``
  / ``in_refund``): for each line, the engine resolves the applicable
  rule by (product category × partner type × foreign flag) and creates
  ``account.move.withholding.line`` entries. The pajak hutang is booked
  via balancing journal items.
- Each withholding line produces a draft ``custom.coretax.bukti.potong``
  with NSFP empty — Coretax fills it after DJP approval.
- Bumps PPh 23 from 2% → 4% automatically for vendors without NPWP.
- Switches to PPh 26 (20%) for foreign-counterparty vendors.

PPN DPP Nilai Lain (PMK 131/2024)
---------------------------------
- ``account.tax`` extended with ``x_custom_dpp_method`` (regular /
  nilai_lain) + ``x_custom_dpp_factor`` (e.g. 11/12 for PPN 11%
  effective via DPP NL 11/12) + ``x_custom_dpp_category`` enumerating
  every PMK 131/2024 category (impor, film, emas perhiasan, kendaraan
  bekas, paket wisata, agen perjalanan, jasa pengiriman, hasil tembakau,
  jasa pemasaran perdagangan, jasa freight forwarding, dst).
- On invoice line tax computation, when ``dpp_method == 'nilai_lain'``
  the tax base is ``price_subtotal * dpp_factor`` instead of the
  full subtotal. This produces the correct effective rate without
  manual workaround.

Faktur Pajak Tools
------------------
- **Faktur Pengganti wizard** — kode status `01` / `02` / ... applied
  in sequence on `account.move.coretax_status`, with NSFP relinking.
- **Bulk pre-export validation wizard** — verifies NPWP (16 or
  15-digit), NIK (16 digit), DPP > 0, sertel attached + not expired
  for a batch of moves before user opens Coretax export wizard.

Audit
-----
Every withholding line creation + Faktur Pengganti relink writes to
``pdp.audit_log`` via ``pdp.audited.mixin``.
""",
    "author": "Custom Platform",
    "website": "https://example.com/custom-platform",
    "category": "Accounting/Localizations",
    "version": "19.0.0.5.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_taxonomy",
        "custom_pdp_audit",
        "custom_coretax",
        "custom_accounting_full",
        "account",
        "base_vat",
        "purchase",
        "product",
    ],
    "capability_tags": ["indonesian-tax", "withholding", "coretax", "audit-trail"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/config_parameters.xml",
        "data/res.country.csv",
        "data/withholding_category_data.xml",
        "data/withholding_rule_seed.xml",
        "data/dpp_nilai_lain_category_data.xml",
        "views/withholding_rule_views.xml",
        "views/withholding_line_views.xml",
        "views/account_tax_views.xml",
        "views/account_move_views.xml",
        "views/res_partner_views.xml",
        "views/res_company_views.xml",
        "views/product_template_views.xml",
        "wizards/faktur_pengganti_wizard_views.xml",
        "wizards/bulk_validation_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
