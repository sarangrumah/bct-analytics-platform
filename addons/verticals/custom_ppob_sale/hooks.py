# -*- coding: utf-8 -*-
"""Post-init hook: seed the PPOB sale-side PPN taxes (PMK-63/2022).

The ERA source shipped these as XML data referencing account/tax-group xmlids
that no longer exist on the platform (accounts are Python-seeded, D5) and carried
an ``era_id_tax_category`` field from era_accounting_edi that the platform does
not have. So we create them here, find-or-create by name, resolving the PPN
Keluaran account from the role mapping for the inclusive variants.

None of these taxes are referenced by xmlid in code -- operators select them on
VA accounts (output_tax_id) or provider DP config (dp_purchase_tax_id) -- so no
stable xmlid is required.
"""

import logging

from odoo import fields

_logger = logging.getLogger(__name__)

TAX_GROUP_NAME = "PPN (Indonesian VAT)"


def _tax_group(env, company):
    Group = env["account.tax.group"].sudo().with_company(company)
    group = Group.search(
        [("name", "=", TAX_GROUP_NAME), ("company_id", "in", (company.id, False))],
        limit=1,
    )
    if not group:
        group = Group.create({"name": TAX_GROUP_NAME, "sequence": 10, "company_id": company.id})
    return group


def _seed_sale_taxes(env, company):
    Tax = env["account.tax"].sudo().with_company(company)
    group = _tax_group(env, company)
    ppn_keluaran = env["custom.ppob.account.mapping"]._get_account("ppn_keluaran", company)
    country = company.account_fiscal_country_id or company.country_id or env.ref("base.id", raise_if_not_found=False)
    Command = fields.Command

    # name -> (price_include_override, route_to_ppn_keluaran, description)
    specs = [
        ("PPN Margin 11% (DPP nilai selisih)", "tax_excluded", False, "PPN atas margin (sell-cost). PMK-63/2022."),
        (
            "PPN DPP Nilai Lain 11% (10/11 x sell)",
            "tax_excluded",
            False,
            "PPN atas DPP nilai lain 10/11 x selling price. PMK-63/2022 kode transaksi 04.",
        ),
        ("PPN Gross 11%", "tax_excluded", False, ""),
        (
            "PPN Gross 11% (Tax-Inclusive)",
            "tax_included",
            True,
            "PPN 11% over gross sell. Price-inclusive variant for DP 100% / Pelunasan flow.",
        ),
        (
            "PPN DPP Nilai Lain 11% (Tax-Inclusive)",
            "tax_included",
            True,
            "PPN atas DPP nilai lain 10/11 x sell. Price-inclusive variant.",
        ),
    ]
    for name, price_include, route, description in specs:
        existing = Tax.search(
            [("name", "=", name), ("company_id", "=", company.id), ("type_tax_use", "=", "sale")],
            limit=1,
        )
        if existing:
            continue
        vals = {
            "name": name,
            "amount_type": "percent",
            "amount": 11.0,
            "type_tax_use": "sale",
            "price_include_override": price_include,
            "tax_group_id": group.id,
            "company_id": company.id,
            "country_id": country.id if country else False,
            "description": description,
        }
        if route and ppn_keluaran:
            vals["invoice_repartition_line_ids"] = [
                Command.create({"document_type": "invoice", "repartition_type": "base", "factor_percent": 100.0}),
                Command.create(
                    {
                        "document_type": "invoice",
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": ppn_keluaran.id,
                    }
                ),
            ]
            vals["refund_repartition_line_ids"] = [
                Command.create({"document_type": "refund", "repartition_type": "base", "factor_percent": 100.0}),
                Command.create(
                    {
                        "document_type": "refund",
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": ppn_keluaran.id,
                    }
                ),
            ]
        Tax.create(vals)


def post_init_hook(env):
    for company in env["res.company"].search([]):
        _seed_sale_taxes(env, company)
    _logger.info("custom_ppob_sale: post_init_hook done")
