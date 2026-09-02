# -*- coding: utf-8 -*-
"""Shared fixtures: two sister companies + minimal chart + an intercompany rule."""

from __future__ import annotations

from odoo.tests.common import TransactionCase


class AccountingFullCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Company = cls.env["res.company"]
        cls.Account = cls.env["account.account"]
        cls.Journal = cls.env["account.journal"]
        cls.Partner = cls.env["res.partner"]
        cls.Move = cls.env["account.move"]
        cls.Rule = cls.env["account.intercompany.rule"]
        cls.Mapping = cls.env["account.intercompany.account.mapping"]
        cls.ConsolConfig = cls.env["account.consolidation.config"]

        # Two sister companies (Company A: alpha, Company B: bravo).
        cls.company_a = cls.Company.create(
            {
                "name": "Alpha Co",
                "currency_id": cls.env.ref("base.IDR").id,
            }
        )
        cls.company_b = cls.Company.create(
            {
                "name": "Bravo Co",
                "currency_id": cls.env.ref("base.IDR").id,
            }
        )

        # Cross-link via res.company.partner_id (used by intercompany detection).
        # On company creation, Odoo creates a partner — we rely on it.
        cls.partner_a_of_b = cls.company_a.partner_id
        cls.partner_b_of_a = cls.company_b.partner_id

        # Minimal accounts on both companies (skip if your account module
        # already provisions a default chart; for tests we just create
        # explicit accounts to avoid relying on country installer behaviour).
        cls.rec_account_a = cls._mk_account(cls.company_a, "11100", "Piutang Usaha", "asset_receivable")
        cls.rec_account_b = cls._mk_account(cls.company_b, "11100", "Piutang Usaha", "asset_receivable")
        cls.pay_account_a = cls._mk_account(cls.company_a, "21100", "Hutang Usaha", "liability_payable")
        cls.pay_account_b = cls._mk_account(cls.company_b, "21100", "Hutang Usaha", "liability_payable")
        cls.rev_account_a = cls._mk_account(cls.company_a, "41000", "Pendapatan", "income")
        cls.rev_account_b = cls._mk_account(cls.company_b, "41000", "Pendapatan", "income")
        cls.exp_account_a = cls._mk_account(cls.company_a, "52000", "Pembelian", "expense_direct_cost")
        cls.exp_account_b = cls._mk_account(cls.company_b, "52000", "Pembelian", "expense_direct_cost")
        # Intercompany clearing pair
        cls.ic_recv_a = cls._mk_account(cls.company_a, "11150", "Piutang IC", "asset_receivable")
        cls.ic_pay_a = cls._mk_account(cls.company_a, "21150", "Hutang IC", "liability_payable")
        cls.ic_recv_b = cls._mk_account(cls.company_b, "11150", "Piutang IC", "asset_receivable")
        cls.ic_pay_b = cls._mk_account(cls.company_b, "21150", "Hutang IC", "liability_payable")

        # One sale journal per company
        cls.sale_journal_a = cls._mk_journal(cls.company_a, "Sales A", "sale", "SAL-A")
        cls.purchase_journal_a = cls._mk_journal(cls.company_a, "Purch A", "purchase", "PUR-A")
        cls.sale_journal_b = cls._mk_journal(cls.company_b, "Sales B", "sale", "SAL-B")
        cls.purchase_journal_b = cls._mk_journal(cls.company_b, "Purch B", "purchase", "PUR-B")

    @classmethod
    def _mk_account(cls, company, code, name, account_type):
        return cls.Account.create(
            {
                "code": code,
                "name": name,
                "account_type": account_type,
                "company_ids": [(6, 0, [company.id])],
            }
        )

    @classmethod
    def _mk_journal(cls, company, name, jtype, code):
        return cls.Journal.create(
            {
                "name": name,
                "type": jtype,
                "code": code,
                "company_id": company.id,
            }
        )
