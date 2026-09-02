# -*- coding: utf-8 -*-
"""Post-init hook: idempotent PPOB chart-of-account scaffolding.

The ERA source shipped these accounts as ``noupdate="1"`` XML data. On Odoo 19
that is a triple hazard: ``account.account.company_ids`` is now M2M (XML data
binds awkwardly), codes are per-company, and re-running ``-u`` is not
idempotent. So we create them in Python instead, resolving each account by
(code, company) at runtime and only creating what is missing -- the same
pattern the platform already uses in
``custom_opening_balance_seed/hooks.py``.

Resolved accounts are recorded role-addressed in ``custom.ppob.account.mapping``
so downstream modules (provider, wallet, sale, commission, va) look accounts up
by a stable role key rather than by a fragile xmlid.
"""

import logging

from odoo.fields import Command

_logger = logging.getLogger(__name__)

# role_key -> (code, name, account_type, reconcile)
PPOB_ACCOUNTS = {
    "cash_bca_escrow": ("1.1.3.01", "Cash - BCA Escrow (VA Settlement)", "asset_cash", True),
    "vendor_advance": ("1.1.6.01", "Vendor Advance Payments (Uang Muka Vendor)", "asset_current", True),
    "ppn_masukan": ("1.1.7.01", "PPN Masukan (Input VAT)", "asset_current", True),
    "commission_receivable": ("1.2.4.01", "Commission Receivable - Provider", "asset_receivable", True),
    "provider_deposit_default": ("1.3.01.00", "Provider Deposit (default holding)", "asset_current", True),
    "customer_advance_payments": (
        "2.1.4.01",
        "Customer Advance Payments (Uang Muka Pelanggan)",
        "liability_current",
        True,
    ),
    "wallet_liab_telko": ("2.1.5.01", "Mitra Wallet Liability - Telko", "liability_current", True),
    "wallet_liab_non_telko": ("2.1.5.02", "Mitra Wallet Liability - Non-Telko", "liability_current", True),
    "commission_payable_mitra": ("2.1.6.01", "Commission Payable - Mitra", "liability_payable", True),
    "pph23_payable": ("2.1.7.01", "PPh 23 Payable (Withheld)", "liability_current", True),
    "ppn_keluaran": ("2.1.8.01", "PPN Keluaran (Output VAT)", "liability_current", True),
    "unidentified_receipts": ("2.3.01.01", "Unidentified Receipts (VA Transit)", "liability_current", True),
    "sales_ppob_telko": ("4.1.1.01", "Sales PPOB - Telko", "income", False),
    "sales_ppob_non_telko": ("4.1.1.02", "Sales PPOB - Non-Telko", "income", False),
    "commission_income": ("4.2.1.01", "Commission Income - Provider", "income_other", False),
    "purchase_discount_income": ("4.2.2.01", "Purchase Discount Received", "income_other", False),
    "cogs_ppob_telko": ("5.1.1.01", "COGS PPOB - Telko", "expense_direct_cost", False),
    "cogs_ppob_non_telko": ("5.1.1.02", "COGS PPOB - Non-Telko", "expense_direct_cost", False),
    "mitra_rebate_expense": ("5.2.1.01", "Mitra Rebate Expense", "expense", False),
}

# code -> (name, sequence, wallet_role, revenue_role, cogs_role, vat_mode)
PPOB_CLASSES = {
    "TELKO": ("Telko (Pulsa, Data, Token)", 10, "wallet_liab_telko", "sales_ppob_telko", "cogs_ppob_telko", "margin"),
    "NON_TELKO": (
        "Non-Telko (Tagihan, BPJS, PLN, PDAM)",
        20,
        "wallet_liab_non_telko",
        "sales_ppob_non_telko",
        "cogs_ppob_non_telko",
        "margin",
    ),
}


def _resolve_account(env, code, company):
    """Find the single account with this code belonging to ``company``."""
    return (
        env["account.account"]
        .with_company(company)
        .search([("code", "=", code), ("company_ids", "in", company.id)], limit=1)
    )


def _ensure_accounts(env, company):
    Account = env["account.account"].with_company(company)
    Mapping = env["custom.ppob.account.mapping"].sudo()
    for role, (code, name, atype, reconcile) in PPOB_ACCOUNTS.items():
        acc = _resolve_account(env, code, company)
        if not acc:
            acc = Account.create(
                {
                    "code": code,
                    "name": name,
                    "account_type": atype,
                    "reconcile": reconcile,
                    "company_ids": [Command.link(company.id)],
                }
            )
            _logger.info("custom_ppob_core: created account %s (%s) for %s", code, atype, company.name)
        mapping = Mapping.search([("company_id", "=", company.id), ("role", "=", role)], limit=1)
        if mapping:
            mapping.account_id = acc.id
        else:
            Mapping.create({"company_id": company.id, "role": role, "account_id": acc.id})


def _ensure_tax_group(env, company):
    """Create the PPN tax group once per company (find-or-create by name)."""
    Group = env["account.tax.group"].sudo().with_company(company)
    existing = Group.search(
        [("name", "=", "PPN (Indonesian VAT)"), ("company_id", "in", (company.id, False))],
        limit=1,
    )
    if not existing:
        Group.create(
            {
                "name": "PPN (Indonesian VAT)",
                "sequence": 10,
                "company_id": company.id,
            }
        )


def _ensure_classes(env, company):
    Mapping = env["custom.ppob.account.mapping"].sudo()
    Class = env["custom.ppob.product.class"].sudo()
    for code, (name, seq, wa, ra, ca, vat) in PPOB_CLASSES.items():
        rec = Class.search([("code", "=", code)], limit=1)
        wallet = Mapping._get_account(wa, company)
        revenue = Mapping._get_account(ra, company)
        cogs = Mapping._get_account(ca, company)
        if rec:
            # Only fill blanks; never clobber operator edits.
            vals = {}
            if not rec.default_wallet_account_id and wallet:
                vals["default_wallet_account_id"] = wallet.id
            if not rec.default_revenue_account_id and revenue:
                vals["default_revenue_account_id"] = revenue.id
            if not rec.default_cogs_account_id and cogs:
                vals["default_cogs_account_id"] = cogs.id
            if vals:
                rec.write(vals)
        else:
            Class.create(
                {
                    "code": code,
                    "name": name,
                    "sequence": seq,
                    "default_wallet_account_id": wallet.id if wallet else False,
                    "default_revenue_account_id": revenue.id if revenue else False,
                    "default_cogs_account_id": cogs.id if cogs else False,
                    "vat_mode": vat,
                }
            )


def post_init_hook(env):
    """Seed PPOB accounts + tax group per company, and product classes once."""
    companies = env["res.company"].search([])
    for company in companies:
        _ensure_accounts(env, company)
        _ensure_tax_group(env, company)
    # Product classes are company-agnostic in the data model; bind them to the
    # main company's accounts (DB-per-tenant deployments have a single company).
    _ensure_classes(env, env.company or companies[:1])
    _logger.info("custom_ppob_core: post_init_hook done (%s companies)", len(companies))
