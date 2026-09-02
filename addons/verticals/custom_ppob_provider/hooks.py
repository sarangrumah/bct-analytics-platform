# -*- coding: utf-8 -*-
"""Post-init hook: seed the purchase-side inclusive PPN tax for DP 100% bills.

The ERA source shipped this tax as XML data referencing account xmlids
(``era_ppob_core.coa_ppn_masukan``) and a tax group xmlid. On the platform
those accounts are created in Python (custom_ppob_core hooks, D5) so no such
xmlids exist. We create the tax here instead -- find-or-create by name,
resolving the PPN Masukan account from the role mapping and the tax group by
name -- which is also idempotent on ``-u`` (Odoo tax CSV/XML data is not).
"""

import logging

from odoo import fields

_logger = logging.getLogger(__name__)

TAX_NAME = "PPN Masukan 11% Inclusive (Coretax)"
TAX_GROUP_NAME = "PPN (Indonesian VAT)"


def _tax_group(env, company):
    Group = env["account.tax.group"].sudo().with_company(company)
    group = Group.search(
        [("name", "=", TAX_GROUP_NAME), ("company_id", "in", (company.id, False))],
        limit=1,
    )
    if not group:
        group = Group.create(
            {
                "name": TAX_GROUP_NAME,
                "sequence": 10,
                "company_id": company.id,
            }
        )
    return group


def _ensure_purchase_ppn_tax(env, company):
    Tax = env["account.tax"].sudo().with_company(company)
    existing = Tax.search(
        [("name", "=", TAX_NAME), ("company_id", "=", company.id), ("type_tax_use", "=", "purchase")],
        limit=1,
    )
    if existing:
        return existing
    ppn_masukan = env["custom.ppob.account.mapping"]._get_account("ppn_masukan", company)
    if not ppn_masukan:
        _logger.warning(
            "custom_ppob_provider: PPN Masukan account not mapped for %s; skipping purchase PPN tax seed.",
            company.name,
        )
        return env["account.tax"]
    group = _tax_group(env, company)
    country = company.account_fiscal_country_id or company.country_id or env.ref("base.id", raise_if_not_found=False)
    Command = fields.Command
    return Tax.create(
        {
            "name": TAX_NAME,
            "amount_type": "percent",
            "amount": 11.0,
            "type_tax_use": "purchase",
            "price_include_override": "tax_included",
            "tax_group_id": group.id,
            "company_id": company.id,
            "country_id": country.id if country else False,
            "description": "Coretax PMK 131/2024 (effective 11% inclusive). "
            "For gross 5,000,000: DPP = gross / 1.11 = 4,504,504.50; "
            "PPN = gross - DPP = 495,495.50.",
            "invoice_repartition_line_ids": [
                Command.create({"document_type": "invoice", "repartition_type": "base", "factor_percent": 100.0}),
                Command.create(
                    {
                        "document_type": "invoice",
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": ppn_masukan.id,
                    }
                ),
            ],
            "refund_repartition_line_ids": [
                Command.create({"document_type": "refund", "repartition_type": "base", "factor_percent": 100.0}),
                Command.create(
                    {
                        "document_type": "refund",
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": ppn_masukan.id,
                    }
                ),
            ],
        }
    )


def post_init_hook(env):
    for company in env["res.company"].search([]):
        _ensure_purchase_ppn_tax(env, company)
    _logger.info("custom_ppob_provider: post_init_hook done")
