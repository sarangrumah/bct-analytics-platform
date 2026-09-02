# -*- coding: utf-8 -*-
"""Indonesian PSAK-aligned chart template (Odoo 19 @template pattern).

Migrated from Odoo 16/17 XML-based ``account.chart.template`` records.
The 5-digit code structure is preserved per PSAK convention:

  1xxxx  Aset (Assets)
  2xxxx  Kewajiban (Liabilities)
  3xxxx  Ekuitas (Equity)
  4xxxx  Pendapatan (Revenue)
  5xxxx  Harga Pokok Penjualan (COGS)
  6xxxx  Beban Operasi (Operating Expenses)
  7xxxx  Pendapatan / Beban Lain (Other Income/Expense)
  8xxxx  Pajak Penghasilan (Income Tax)

Template body ships:
  - 1 tax group (PPN)
  - 12 account groups (hierarchy: 1, 11, 12, 2, 21, 22, 3, 4, 5, 6, 7, 8)
  - 55 accounts across all PSAK categories
  - 2 PPN taxes (11% keluaran / masukan — PMK 58/2022 rate, since 1 Apr 2022)
  - 6 journals (INV, BILL, CASH, BANK, MISC, EXCH) with Indonesian labels
  - 2 fiscal positions (Ekspor, Pelanggan Bebas Pajak)
"""

from odoo import Command, models
from odoo.addons.account.models.chart_template import template


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    # ------------------------------------------------------------------
    # Root template metadata
    # ------------------------------------------------------------------

    @template("id_psak")
    def _get_id_psak_template_data(self):
        return {
            "name": "Indonesia (PSAK) — Custom Platform",
            "code_digits": "5",
            "use_anglo_saxon": False,
            "property_account_receivable_id": "account_id_psak_11100",
            "property_account_payable_id": "account_id_psak_21100",
            "property_account_expense_categ_id": "account_id_psak_52000",
            "property_account_income_categ_id": "account_id_psak_41000",
            "country_id": "base.id",
        }

    @template("id_psak", "res.company")
    def _get_id_psak_res_company(self):
        return {
            self.env.company.id: {
                "anglo_saxon_accounting": False,
                "account_fiscal_country_id": "base.id",
                "bank_account_code_prefix": "11020",
                "cash_account_code_prefix": "11010",
                "transfer_account_code_prefix": "11030",
                "account_default_pos_receivable_account_id": "account_id_psak_11200",
                "income_currency_exchange_account_id": "account_id_psak_71100",
                "expense_currency_exchange_account_id": "account_id_psak_72100",
                "account_journal_suspense_account_id": "account_id_psak_11200",
                "default_cash_difference_income_account_id": "account_id_psak_71000",
                "default_cash_difference_expense_account_id": "account_id_psak_68000",
                "account_sale_tax_id": "account_id_psak_tax_ppn_keluaran_11",
                "account_purchase_tax_id": "account_id_psak_tax_ppn_masukan_11",
            },
        }

    # ------------------------------------------------------------------
    # Tax groups (loaded before taxes)
    # ------------------------------------------------------------------

    @template("id_psak", "account.tax.group")
    def _get_id_psak_account_tax_group(self):
        return {
            "account_id_psak_tax_group_ppn": {
                "name": "PPN",
                "sequence": 1,
            },
        }

    # ------------------------------------------------------------------
    # Account groups — hierarchical buckets for nested report display
    # ------------------------------------------------------------------

    @template("id_psak", "account.group")
    def _get_id_psak_account_group(self):
        return {
            "account_group_id_psak_1": {"name": "Aset", "code_prefix_start": "1", "code_prefix_end": "1"},
            "account_group_id_psak_11": {"name": "Aset Lancar", "code_prefix_start": "11", "code_prefix_end": "11"},
            "account_group_id_psak_12": {
                "name": "Aset Tidak Lancar",
                "code_prefix_start": "12",
                "code_prefix_end": "13",
            },
            "account_group_id_psak_2": {"name": "Kewajiban", "code_prefix_start": "2", "code_prefix_end": "2"},
            "account_group_id_psak_21": {
                "name": "Kewajiban Lancar",
                "code_prefix_start": "21",
                "code_prefix_end": "21",
            },
            "account_group_id_psak_22": {
                "name": "Kewajiban Jangka Panjang",
                "code_prefix_start": "22",
                "code_prefix_end": "22",
            },
            "account_group_id_psak_3": {"name": "Ekuitas", "code_prefix_start": "3", "code_prefix_end": "3"},
            "account_group_id_psak_4": {"name": "Pendapatan", "code_prefix_start": "4", "code_prefix_end": "4"},
            "account_group_id_psak_5": {
                "name": "Harga Pokok Penjualan",
                "code_prefix_start": "5",
                "code_prefix_end": "5",
            },
            "account_group_id_psak_6": {"name": "Beban Operasional", "code_prefix_start": "6", "code_prefix_end": "6"},
            "account_group_id_psak_7": {
                "name": "Pendapatan / Beban Lain",
                "code_prefix_start": "7",
                "code_prefix_end": "7",
            },
            "account_group_id_psak_8": {"name": "Pajak Penghasilan", "code_prefix_start": "8", "code_prefix_end": "8"},
        }

    # ------------------------------------------------------------------
    # Account chart — 55 accounts across all PSAK categories
    # ------------------------------------------------------------------

    @template("id_psak", "account.account")
    def _get_id_psak_account_account(self):
        return {
            # ============================================================
            # 1xxxx — ASET (Assets)
            # ============================================================
            # 11xxx Aset Lancar
            "account_id_psak_11010": {"name": "Kas", "code": "11010", "account_type": "asset_cash"},
            "account_id_psak_11020": {"name": "Bank", "code": "11020", "account_type": "asset_cash"},
            "account_id_psak_11030": {
                "name": "Kas/Bank dalam Perjalanan",
                "code": "11030",
                "account_type": "asset_current",
            },
            "account_id_psak_11100": {
                "name": "Piutang Usaha",
                "code": "11100",
                "account_type": "asset_receivable",
                "reconcile": True,
            },
            "account_id_psak_11200": {
                "name": "Piutang Lainnya",
                "code": "11200",
                "account_type": "asset_receivable",
                "reconcile": True,
            },
            "account_id_psak_11300": {
                "name": "Persediaan Barang Dagangan",
                "code": "11300",
                "account_type": "asset_current",
            },
            "account_id_psak_11310": {
                "name": "Persediaan Bahan Baku",
                "code": "11310",
                "account_type": "asset_current",
            },
            "account_id_psak_11320": {
                "name": "Persediaan Barang Jadi",
                "code": "11320",
                "account_type": "asset_current",
            },
            "account_id_psak_11400": {
                "name": "Beban Dibayar Dimuka",
                "code": "11400",
                "account_type": "asset_prepayments",
            },
            "account_id_psak_11500": {"name": "PPN Masukan", "code": "11500", "account_type": "asset_current"},
            # 12xxx Aset Tidak Lancar
            "account_id_psak_12100": {"name": "Tanah", "code": "12100", "account_type": "asset_fixed"},
            "account_id_psak_12200": {"name": "Bangunan", "code": "12200", "account_type": "asset_fixed"},
            "account_id_psak_12300": {"name": "Mesin & Peralatan", "code": "12300", "account_type": "asset_fixed"},
            "account_id_psak_12400": {"name": "Kendaraan", "code": "12400", "account_type": "asset_fixed"},
            "account_id_psak_12500": {"name": "Inventaris Kantor", "code": "12500", "account_type": "asset_fixed"},
            "account_id_psak_12900": {"name": "Akumulasi Penyusutan", "code": "12900", "account_type": "asset_fixed"},
            "account_id_psak_13000": {
                "name": "Aset Tidak Berwujud",
                "code": "13000",
                "account_type": "asset_non_current",
            },
            # ============================================================
            # 2xxxx — KEWAJIBAN (Liabilities)
            # ============================================================
            # 21xxx Kewajiban Lancar
            "account_id_psak_21100": {
                "name": "Hutang Usaha",
                "code": "21100",
                "account_type": "liability_payable",
                "reconcile": True,
            },
            "account_id_psak_21200": {
                "name": "Hutang Lain-lain",
                "code": "21200",
                "account_type": "liability_payable",
                "reconcile": True,
            },
            "account_id_psak_21300": {"name": "Hutang Gaji", "code": "21300", "account_type": "liability_current"},
            "account_id_psak_21400": {
                "name": "Hutang PPN Keluaran",
                "code": "21400",
                "account_type": "liability_current",
            },
            "account_id_psak_21500": {"name": "Hutang PPh", "code": "21500", "account_type": "liability_current"},
            "account_id_psak_21900": {
                "name": "Hutang Jangka Pendek Lainnya",
                "code": "21900",
                "account_type": "liability_current",
            },
            # 22xxx Kewajiban Jangka Panjang
            "account_id_psak_22100": {
                "name": "Hutang Bank Jangka Panjang",
                "code": "22100",
                "account_type": "liability_non_current",
            },
            "account_id_psak_22200": {
                "name": "Hutang Jangka Panjang Lainnya",
                "code": "22200",
                "account_type": "liability_non_current",
            },
            # ============================================================
            # 3xxxx — EKUITAS (Equity)
            # ============================================================
            "account_id_psak_31000": {"name": "Modal Disetor", "code": "31000", "account_type": "equity"},
            "account_id_psak_32000": {"name": "Laba Ditahan", "code": "32000", "account_type": "equity"},
            "account_id_psak_33000": {
                "name": "Laba/Rugi Tahun Berjalan",
                "code": "33000",
                "account_type": "equity_unaffected",
            },
            "account_id_psak_34000": {"name": "Prive / Pengambilan Pemilik", "code": "34000", "account_type": "equity"},
            # ============================================================
            # 4xxxx — PENDAPATAN (Revenue)
            # ============================================================
            "account_id_psak_41000": {"name": "Pendapatan Usaha", "code": "41000", "account_type": "income"},
            "account_id_psak_42000": {"name": "Pendapatan Jasa", "code": "42000", "account_type": "income"},
            "account_id_psak_43000": {"name": "Diskon Penjualan", "code": "43000", "account_type": "income"},
            "account_id_psak_44000": {"name": "Retur Penjualan", "code": "44000", "account_type": "income"},
            # ============================================================
            # 5xxxx — HARGA POKOK PENJUALAN (COGS)
            # ============================================================
            "account_id_psak_51000": {
                "name": "HPP Barang Dagangan",
                "code": "51000",
                "account_type": "expense_direct_cost",
            },
            "account_id_psak_52000": {"name": "HPP Jasa", "code": "52000", "account_type": "expense_direct_cost"},
            "account_id_psak_53000": {"name": "HPP Bahan Baku", "code": "53000", "account_type": "expense_direct_cost"},
            # ============================================================
            # 6xxxx — BEBAN OPERASIONAL (Operating Expenses)
            # ============================================================
            "account_id_psak_61000": {"name": "Beban Gaji & Tunjangan", "code": "61000", "account_type": "expense"},
            "account_id_psak_62000": {"name": "Beban Sewa", "code": "62000", "account_type": "expense"},
            "account_id_psak_63000": {
                "name": "Beban Utilitas (Listrik/Air/Telp)",
                "code": "63000",
                "account_type": "expense",
            },
            "account_id_psak_64000": {"name": "Beban Perlengkapan Kantor", "code": "64000", "account_type": "expense"},
            "account_id_psak_65000": {"name": "Beban Pemasaran & Promosi", "code": "65000", "account_type": "expense"},
            "account_id_psak_66000": {
                "name": "Beban Penyusutan",
                "code": "66000",
                "account_type": "expense_depreciation",
            },
            "account_id_psak_67000": {"name": "Beban Administrasi", "code": "67000", "account_type": "expense"},
            "account_id_psak_68000": {"name": "Beban Selisih Kas", "code": "68000", "account_type": "expense"},
            "account_id_psak_69000": {"name": "Beban Operasional Lainnya", "code": "69000", "account_type": "expense"},
            # ============================================================
            # 7xxxx — PENDAPATAN / BEBAN LAIN-LAIN (Other Income/Expense)
            # ============================================================
            "account_id_psak_71000": {"name": "Pendapatan Lain-lain", "code": "71000", "account_type": "income_other"},
            "account_id_psak_71100": {
                "name": "Selisih Kurs - Keuntungan",
                "code": "71100",
                "account_type": "income_other",
            },
            "account_id_psak_71200": {"name": "Pendapatan Bunga", "code": "71200", "account_type": "income_other"},
            "account_id_psak_72000": {"name": "Beban Lain-lain", "code": "72000", "account_type": "expense"},
            "account_id_psak_72100": {"name": "Selisih Kurs - Kerugian", "code": "72100", "account_type": "expense"},
            "account_id_psak_72200": {"name": "Beban Bunga", "code": "72200", "account_type": "expense"},
            # ============================================================
            # 8xxxx — PAJAK PENGHASILAN (Income Tax)
            # ============================================================
            "account_id_psak_81000": {
                "name": "Beban Pajak Penghasilan Badan",
                "code": "81000",
                "account_type": "expense",
            },
            "account_id_psak_82000": {"name": "Beban PPh Final", "code": "82000", "account_type": "expense"},
        }

    # ------------------------------------------------------------------
    # Taxes — Indonesian PPN at 11% (PMK 58/2022, since 1 April 2022)
    # ------------------------------------------------------------------

    @template("id_psak", "account.tax")
    def _get_id_psak_account_tax(self):
        return {
            "account_id_psak_tax_ppn_keluaran_11": {
                "name": "PPN Keluaran 11%",
                "description": "PPN 11%",
                "amount": 11.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "tax_group_id": "account_id_psak_tax_group_ppn",
                "invoice_repartition_line_ids": [
                    Command.create({"factor_percent": 100, "repartition_type": "base"}),
                    Command.create(
                        {
                            "factor_percent": 100,
                            "repartition_type": "tax",
                            "account_id": "account_id_psak_21400",
                        }
                    ),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"factor_percent": 100, "repartition_type": "base"}),
                    Command.create(
                        {
                            "factor_percent": 100,
                            "repartition_type": "tax",
                            "account_id": "account_id_psak_21400",
                        }
                    ),
                ],
            },
            "account_id_psak_tax_ppn_masukan_11": {
                "name": "PPN Masukan 11%",
                "description": "PPN 11%",
                "amount": 11.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "tax_group_id": "account_id_psak_tax_group_ppn",
                "invoice_repartition_line_ids": [
                    Command.create({"factor_percent": 100, "repartition_type": "base"}),
                    Command.create(
                        {
                            "factor_percent": 100,
                            "repartition_type": "tax",
                            "account_id": "account_id_psak_11500",
                        }
                    ),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"factor_percent": 100, "repartition_type": "base"}),
                    Command.create(
                        {
                            "factor_percent": 100,
                            "repartition_type": "tax",
                            "account_id": "account_id_psak_11500",
                        }
                    ),
                ],
            },
        }

    # ------------------------------------------------------------------
    # Journals — Indonesian-named overrides of Odoo defaults
    # ------------------------------------------------------------------

    @template("id_psak", "account.journal")
    def _get_id_psak_account_journal(self):
        return {
            "account_id_psak_journal_sale": {
                "name": "Faktur Penjualan",
                "code": "INV",
                "type": "sale",
                "show_on_dashboard": True,
                "sequence": 5,
            },
            "account_id_psak_journal_purchase": {
                "name": "Tagihan Pembelian",
                "code": "BILL",
                "type": "purchase",
                "show_on_dashboard": True,
                "sequence": 6,
            },
            "account_id_psak_journal_cash": {
                "name": "Kas",
                "code": "CASH",
                "type": "cash",
                "show_on_dashboard": True,
                "sequence": 7,
            },
            "account_id_psak_journal_bank": {
                "name": "Bank",
                "code": "BANK",
                "type": "bank",
                "show_on_dashboard": True,
                "sequence": 8,
            },
            "account_id_psak_journal_misc": {
                "name": "Jurnal Umum",
                "code": "MISC",
                "type": "general",
                "show_on_dashboard": False,
                "sequence": 9,
            },
            "account_id_psak_journal_exch": {
                "name": "Selisih Kurs",
                "code": "EXCH",
                "type": "general",
                "show_on_dashboard": False,
                "sequence": 10,
            },
        }

    # ------------------------------------------------------------------
    # Fiscal positions — common Indonesian VAT scenarios
    # ------------------------------------------------------------------

    @template("id_psak", "account.fiscal.position")
    def _get_id_psak_account_fiscal_position(self):
        return {
            "account_id_psak_fpos_ekspor": {
                "name": "Ekspor (Tanpa PPN)",
                "auto_apply": False,
                "tax_ids": [
                    Command.create(
                        {
                            "tax_src_id": "account_id_psak_tax_ppn_keluaran_11",
                            "tax_dest_id": False,
                        }
                    ),
                ],
            },
            "account_id_psak_fpos_bebas_pajak": {
                "name": "Pelanggan Bebas Pajak",
                "auto_apply": False,
                "tax_ids": [
                    Command.create(
                        {
                            "tax_src_id": "account_id_psak_tax_ppn_keluaran_11",
                            "tax_dest_id": False,
                        }
                    ),
                    Command.create(
                        {
                            "tax_src_id": "account_id_psak_tax_ppn_masukan_11",
                            "tax_dest_id": False,
                        }
                    ),
                ],
            },
        }
