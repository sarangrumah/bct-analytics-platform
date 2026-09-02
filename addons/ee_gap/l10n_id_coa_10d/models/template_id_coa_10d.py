# -*- coding: utf-8 -*-
"""10-digit chart template (code ``id_coa_10d``).

Bulk data (accounts, account groups, tax groups, taxes) is shipped as CSV under
``data/template/*-id_coa_10d.csv`` and loaded by the chart-template engine. This
module supplies the root metadata, company defaults, journals and fiscal
positions in Python.
"""

from odoo import models
from odoo.addons.account.models.chart_template import template


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    @template("id_coa_10d")
    def _get_id_coa_10d_template_data(self):
        return {
            "name": "Indonesia 10-Digit",
            "country": "id",
            "code_digits": "10",
            "country_id": "base.id",
            "use_storno_accounting": False,
            "property_account_receivable_id": "id_coa_10d_1106000001",
            "property_account_payable_id": "id_coa_10d_2103100001",
            "property_account_income_categ_id": "id_coa_10d_5199000000",
            "property_account_expense_categ_id": "id_coa_10d_6199000000",
        }

    @template("id_coa_10d", "res.company")
    def _get_id_coa_10d_res_company(self):
        return {
            self.env.company.id: {
                "anglo_saxon_accounting": True,
                "account_fiscal_country_id": "base.id",
                "bank_account_code_prefix": "1103",
                "cash_account_code_prefix": "1102",
                "transfer_account_code_prefix": "1101",
                "income_currency_exchange_account_id": "id_coa_10d_7607000000",
                "expense_currency_exchange_account_id": "id_coa_10d_7704000000",
                "account_sale_tax_id": "id_coa_10d_tax_12_non_luxury_good_sale",
                "account_purchase_tax_id": "id_coa_10d_tax_12_non_luxury_good_purchase",
            },
        }

    @template("id_coa_10d", "account.journal")
    def _get_id_coa_10d_account_journal(self):
        # Override the standard journals the base `account` template creates
        # (keys: sale/purchase/general/bank/cash) rather than adding new ones,
        # which would duplicate journals and collide on mail aliases.
        return {
            "sale": {"name": "Penjualan", "default_account_id": "id_coa_10d_5199000000"},
            "purchase": {"name": "Pembelian", "default_account_id": "id_coa_10d_6199000000"},
        }

    @template("id_coa_10d", "account.fiscal.position")
    def _get_id_coa_10d_account_fiscal_position(self):
        return {
            "id_coa_10d_fpos_domestic": {
                "name": "Domestik",
                "auto_apply": True,
                "country_id": "base.id",
            },
            "id_coa_10d_fpos_foreign": {
                "name": "Luar Negeri / Ekspor",
                "auto_apply": False,
            },
        }
