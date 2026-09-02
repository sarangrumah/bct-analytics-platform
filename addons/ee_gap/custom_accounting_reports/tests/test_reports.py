# -*- coding: utf-8 -*-
"""Production-grade test suite for ``custom_accounting_reports``.

Strategy
--------
Each test seeds its own minimal company + chart of accounts (5–10
accounts, two journals, a handful of partners) and posts moves through
the ORM so the resulting ``account.move.line`` rows feed the engine's
raw-SQL aggregator exactly as in production.

We never rely on the demo data — modules in this repo ship without it
(see ``__manifest__.py``).
"""

from __future__ import annotations

from datetime import date, timedelta

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCustomReports(TransactionCase):
    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env["account.account"]
        cls.Journal = cls.env["account.journal"]
        cls.Partner = cls.env["res.partner"]
        cls.Move = cls.env["account.move"]

        cls.company = cls.env["res.company"].create(
            {
                "name": "Reports Test Co",
                "currency_id": cls.env.ref("base.IDR").id,
                # Fiscal country must match the taxes' country_id (Odoo 19
                # enforces account.move._validate_taxes_country).
                "country_id": cls.env.ref("base.id").id,
            }
        )
        cls.env.user.write(
            {
                "company_ids": [Command.link(cls.company.id)],
                "company_id": cls.company.id,
            }
        )
        # Re-bind env so subsequent reads honour the new company. Odoo 19's
        # ``Environment`` has no ``with_company`` (that lives on recordsets);
        # binding ``allowed_company_ids`` in the context is the equivalent —
        # it makes ``cls.env.company`` resolve to the test company.
        cls.env = cls.env(
            user=cls.env.user,
            su=True,
            context=dict(cls.env.context, allowed_company_ids=[cls.company.id]),
        )

        cls.acc_cash = cls._mk_account("11000", "Cash", "asset_cash")
        cls.acc_recv = cls._mk_account(
            "11100",
            "Receivables",
            "asset_receivable",
        )
        cls.acc_pay = cls._mk_account(
            "21100",
            "Payables",
            "liability_payable",
        )
        cls.acc_equity = cls._mk_account(
            "31000",
            "Owner Equity",
            "equity",
        )
        cls.acc_revenue = cls._mk_account(
            "41000",
            "Service Revenue",
            "income",
        )
        cls.acc_expense = cls._mk_account(
            "52000",
            "Operating Expense",
            "expense",
        )

        cls.j_misc = cls.Journal.create(
            {
                "name": "Miscellaneous",
                "code": "MISC",
                "type": "general",
                "company_id": cls.company.id,
            }
        )

        cls.j_sale = cls.Journal.create(
            {
                "name": "Sales",
                "code": "SAL",
                "type": "sale",
                "company_id": cls.company.id,
            }
        )

        cls.partner_a = cls.Partner.create({"name": "Customer A"})
        cls.partner_b = cls.Partner.create({"name": "Customer B"})

        # Odoo 19 makes account.tax.tax_group_id mandatory (not-null).
        cls.tax_group = cls.env["account.tax.group"].create({"name": "Test Taxes", "company_id": cls.company.id})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _mk_account(cls, code, name, account_type):
        return cls.Account.create(
            {
                "code": code,
                "name": name,
                "account_type": account_type,
                "company_ids": [Command.link(cls.company.id)],
            }
        )

    def _post_move(
        self,
        lines,
        dt=None,
        partner=None,
        ref=None,
    ):
        """Create + post an ``account.move`` with the given line tuples.

        ``lines`` is ``[(account, debit, credit), ...]``.
        """
        dt = dt or date.today()
        line_vals = []
        for account, debit, credit in lines:
            line_vals.append(
                Command.create(
                    {
                        "account_id": account.id,
                        "name": ref or "test",
                        "debit": debit,
                        "credit": credit,
                        "partner_id": partner.id if partner else False,
                    }
                )
            )
        move = self.Move.create(
            {
                "journal_id": self.j_misc.id,
                "date": dt,
                "company_id": self.company.id,
                "partner_id": partner.id if partner else False,
                "ref": ref,
                "line_ids": line_vals,
            }
        )
        move.action_post()
        return move

    def _filters(self, **overrides):
        today = date.today()
        defaults = {
            "date_from": today.replace(month=1, day=1),
            "date_to": today,
            "company_ids": [self.company.id],
            "journal_ids": [],
            "account_ids": [],
            "partner_ids": [],
            "posted_only": True,
        }
        defaults.update(overrides)
        return defaults

    # ------------------------------------------------------------------
    # 1) Trial balance sums to zero (debit total == credit total).
    # ------------------------------------------------------------------
    def test_trial_balance_zero_sum(self):
        today = date.today()
        # Five balanced moves spanning all six accounts.
        self._post_move(
            [
                (self.acc_cash, 1000.0, 0.0),
                (self.acc_equity, 0.0, 1000.0),
            ],
            dt=today,
            ref="m1",
        )
        self._post_move(
            [
                (self.acc_recv, 500.0, 0.0),
                (self.acc_revenue, 0.0, 500.0),
            ],
            dt=today,
            partner=self.partner_a,
            ref="m2",
        )
        self._post_move(
            [
                (self.acc_cash, 500.0, 0.0),
                (self.acc_recv, 0.0, 500.0),
            ],
            dt=today,
            partner=self.partner_a,
            ref="m3",
        )
        self._post_move(
            [
                (self.acc_expense, 200.0, 0.0),
                (self.acc_cash, 0.0, 200.0),
            ],
            dt=today,
            ref="m4",
        )
        self._post_move(
            [
                (self.acc_expense, 100.0, 0.0),
                (self.acc_pay, 0.0, 100.0),
            ],
            dt=today,
            partner=self.partner_b,
            ref="m5",
        )

        tb = self.env["custom.report.trial.balance"]
        lines = tb._build_lines(self._filters())
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(
            grand["movement_debit"],
            grand["movement_credit"],
            places=2,
            msg="Trial-balance debit total must equal credit total.",
        )
        self.assertGreater(
            grand["movement_debit"],
            0.0,
            "Trial balance must aggregate non-zero movement.",
        )

    # ------------------------------------------------------------------
    # 2) Aged receivable: 4 invoices spread across all 5 buckets.
    #    Buckets defined in custom_report_aged_receivable.BUCKETS.
    # ------------------------------------------------------------------
    def test_aged_receivable_buckets(self):
        today = date.today()
        cases = [
            # (days_overdue, amount, expected_bucket_code)
            (-5, 100.0, "not_due"),  # due in the future
            (10, 200.0, "d_0_30"),  # 1..30
            (45, 300.0, "d_31_60"),  # 31..60
            (80, 400.0, "d_61_90"),  # 61..90
            (120, 500.0, "d_91_180"),  # 91..180 (BUCKETS now also has 181-365)
        ]
        partners = []
        for i, (overdue, amount, _bucket) in enumerate(cases):
            partner = self.Partner.create({"name": f"Aging P{i}"})
            partners.append(partner)
            due = today - timedelta(days=overdue)
            move = self.Move.create(
                {
                    "journal_id": self.j_misc.id,
                    "date": today - timedelta(days=max(overdue, 0)),
                    "invoice_date_due": due,
                    "company_id": self.company.id,
                    "partner_id": partner.id,
                    "line_ids": [
                        Command.create(
                            {
                                "account_id": self.acc_recv.id,
                                "name": "aging",
                                "debit": amount,
                                "credit": 0.0,
                                "partner_id": partner.id,
                                "date_maturity": due,
                            }
                        ),
                        Command.create(
                            {
                                "account_id": self.acc_revenue.id,
                                "name": "aging",
                                "debit": 0.0,
                                "credit": amount,
                                "partner_id": partner.id,
                            }
                        ),
                    ],
                }
            )
            move.action_post()

        ar = self.env["custom.report.aged.receivable"]
        result = ar._build_lines(self._filters())
        per_partner = {r["partner_name"]: r for r in result["rows"]}

        for i, (_overdue, amount, expected_bucket) in enumerate(cases):
            row = per_partner.get(f"Aging P{i}")
            self.assertIsNotNone(
                row,
                f"Partner P{i} must appear in aged receivable.",
            )
            self.assertAlmostEqual(
                row[expected_bucket],
                amount,
                places=2,
                msg=(f"Partner P{i}: expected {amount} in bucket {expected_bucket}, got {row[expected_bucket]}."),
            )

    # ------------------------------------------------------------------
    # 3) General Ledger respects the partner filter.
    # ------------------------------------------------------------------
    def test_general_ledger_partner_filter(self):
        today = date.today()
        self._post_move(
            [
                (self.acc_recv, 1000.0, 0.0),
                (self.acc_revenue, 0.0, 1000.0),
            ],
            dt=today,
            partner=self.partner_a,
            ref="A1",
        )
        self._post_move(
            [
                (self.acc_recv, 2000.0, 0.0),
                (self.acc_revenue, 0.0, 2000.0),
            ],
            dt=today,
            partner=self.partner_b,
            ref="B1",
        )

        gl = self.env["custom.report.general.ledger"]
        lines = gl._build_lines(self._filters(partner_ids=[self.partner_a.id]))
        # Find Receivables account section.
        ar_section = next(
            (l for l in lines if l.get("type") == "account" and l.get("account_code") == "11100"),
            None,
        )
        self.assertIsNotNone(
            ar_section,
            "Receivables section must appear in filtered GL.",
        )
        # Every line in the section must reference Customer A only.
        for entry in ar_section["lines"]:
            self.assertEqual(
                entry.get("partner"),
                self.partner_a.name,
                "Partner filter must exclude other partners.",
            )

    # ------------------------------------------------------------------
    # 4) Balance Sheet equation: assets = liabilities + equity (+/- P&L).
    # ------------------------------------------------------------------
    def test_balance_sheet_equation(self):
        today = date.today()
        # Owner contributes 10_000 cash.
        self._post_move(
            [
                (self.acc_cash, 10000.0, 0.0),
                (self.acc_equity, 0.0, 10000.0),
            ],
            dt=today,
            ref="contrib",
        )
        # Borrows 3_000.
        self._post_move(
            [
                (self.acc_cash, 3000.0, 0.0),
                (self.acc_pay, 0.0, 3000.0),
            ],
            dt=today,
            ref="borrow",
        )
        # Earns 2_000 revenue.
        self._post_move(
            [
                (self.acc_recv, 2000.0, 0.0),
                (self.acc_revenue, 0.0, 2000.0),
            ],
            dt=today,
            partner=self.partner_a,
            ref="sale",
        )
        # Pays 500 expense.
        self._post_move(
            [
                (self.acc_expense, 500.0, 0.0),
                (self.acc_cash, 0.0, 500.0),
            ],
            dt=today,
            ref="opex",
        )

        bs = self.env["custom.report.balance.sheet"]
        lines = bs._build_lines(self._filters())

        total_assets = next(l for l in lines if l.get("type") == "total" and l.get("label") == "Total Assets")[
            "signed_balance"
        ]
        total_liab = next(l for l in lines if l.get("type") == "total" and l.get("label") == "Total Liabilities")[
            "signed_balance"
        ]
        total_eq = next(l for l in lines if l.get("type") == "total" and l.get("label") == "Total Equity")[
            "signed_balance"
        ]

        # The Balance Sheet reports the un-closed period result as a
        # "Current Year Earnings" equity line, so the accounting equation
        # holds even before the period is closed.
        pl = self.env["custom.report.profit.loss"]
        pl_lines = pl._build_lines(self._filters())
        net_profit = next(l for l in pl_lines if l.get("type") == "grand_total")["signed_balance"]

        earnings = next(l for l in lines if l.get("label") == "Current Year Earnings")["signed_balance"]
        self.assertAlmostEqual(
            earnings,
            net_profit,
            places=2,
            msg="Current Year Earnings must equal the period's Net Profit.",
        )
        self.assertAlmostEqual(
            total_assets - total_liab - total_eq,
            0.0,
            places=2,
            msg="Assets must equal Liabilities + Equity once earnings are included.",
        )

    # ------------------------------------------------------------------
    # 5) Tax report subtotals per fiscal position sum to grand total.
    # ------------------------------------------------------------------
    def test_tax_report_subtotals(self):
        """The invariant ``sum(category.tax_subtotal) == grand_total.tax_amount``
        must hold. We post one PPN-out invoice + one PPh-23 vendor bill to
        exercise both the 'output' and 'withholding' branches.
        """
        today = date.today()
        acc_ppn = self._mk_account("21250", "PPN Out Sub", "liability_current")
        acc_pph = self._mk_account("21260", "PPh 23 Sub", "liability_current")
        ppn = self._mk_ppn_tax("PPN Out 11%", "sale", acc_ppn)
        pph = self._mk_ppn_tax("PPh 23 2%", "purchase", acc_pph, amount=2.0)
        j_purchase = self.Journal.create(
            {"name": "Purchases Sub", "code": "BLL2", "type": "purchase", "company_id": self.company.id}
        )
        # Output side: a customer invoice bearing PPN.
        self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": self.partner_a.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Barang",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [Command.set([ppn.id])],
                        }
                    )
                ],
            }
        ).action_post()
        # Withholding side: a vendor bill bearing PPh 23.
        self.Move.create(
            {
                "move_type": "in_invoice",
                "journal_id": j_purchase.id,
                "partner_id": self.partner_b.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Jasa",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.acc_expense.id,
                            "tax_ids": [Command.set([pph.id])],
                        }
                    )
                ],
            }
        ).action_post()

        report = self.env["custom.report.tax"]
        lines = report._build_lines(self._filters())
        grand = next(
            (l for l in lines if l.get("type") == "grand_total"),
            None,
        )
        self.assertIsNotNone(grand, "Tax report must emit a grand_total.")
        category_subtotal = sum(l["tax_subtotal"] for l in lines if l.get("type") == "category")
        self.assertAlmostEqual(
            category_subtotal,
            grand["tax_amount"],
            places=2,
            msg="Category subtotals must sum to grand tax_amount.",
        )

    # ------------------------------------------------------------------
    # 6) General Ledger flat layout: one row per line, account as a column,
    #    column picker honoured.
    # ------------------------------------------------------------------
    def test_general_ledger_flat_layout(self):
        today = date.today()
        self._post_move(
            [(self.acc_recv, 1000.0, 0.0), (self.acc_revenue, 0.0, 1000.0)],
            dt=today,
            partner=self.partner_a,
            ref="F1",
        )

        gl_all = self.env["custom.report.general.ledger"].with_context(gl_layout="flat")
        lines = gl_all._build_lines(self._filters())
        detail = [l for l in lines if l.get("type") != "grand_total"]
        self.assertTrue(detail, "Flat GL must emit per-line rows.")
        self.assertTrue(
            all("account_code" in l for l in detail),
            "Flat GL rows must carry the account as a field/column.",
        )
        self.assertEqual(lines[-1].get("type"), "grand_total")

        # No optional columns selected -> 9 core columns only.
        gl_core = self.env["custom.report.general.ledger"].with_context(gl_layout="flat", gl_columns=[])
        # 10 core columns -- the branch column is always shown.
        self.assertEqual(len(gl_core._xlsx_columns()), 10)
        # All optional columns selected -> 10 core + 10 optional = 20.
        gl_full = self.env["custom.report.general.ledger"].with_context(
            gl_layout="flat",
            gl_columns=[
                "doc_no",
                "reference",
                "tax",
                "clearing",
                "cost_center",
                "profit_center",
                "currency",
                "amount_currency",
                "due_date",
                "user",
            ],
        )
        self.assertEqual(len(gl_full._xlsx_columns()), 20)
        # Grouped layout: Date/Entry/Partner/Label/Branch + Debit/Credit/Balance.
        gl_grouped = self.env["custom.report.general.ledger"]
        self.assertEqual(len(gl_grouped._xlsx_columns()), 8)

    def test_grouped_screen_tables_carry_values(self):
        """Reports that nest their movements under a group row (GL,
        Partner Ledger) must render those movements on screen — the
        default flattener used to emit one blank row per group while the
        Excel export looked fine."""
        today = date.today()
        self._post_move(
            [(self.acc_recv, 1000.0, 0.0), (self.acc_revenue, 0.0, 1000.0)],
            dt=today,
            partner=self.partner_a,
            ref="SCREEN1",
        )
        options = {
            "date_from": date(today.year, 1, 1).isoformat(),
            "date_to": today.isoformat(),
            "company_ids": [self.company.id],
            "posted_only": True,
        }
        for model, group_label in (
            ("custom.report.general.ledger", "["),
            ("custom.report.partner.ledger", self.partner_a.name),
        ):
            table = self.env[model].get_report_table(options)
            types = [row["type"] for row in table["lines"]]
            self.assertIn("group", types, "%s must emit group heading rows." % model)
            data_rows = [row for row in table["lines"] if row["type"] == "data"]
            self.assertTrue(data_rows, "%s must emit its nested movements." % model)
            self.assertTrue(
                any(v not in (None, "", 0) for row in data_rows for v in row["values"].values()),
                "%s movement rows must carry values, not blanks." % model,
            )
            heading = next(row for row in table["lines"] if row["type"] == "group")
            self.assertTrue(
                any(group_label in str(v) for v in heading["values"].values()),
                "%s heading row must name its group." % model,
            )
            self.assertEqual(types[-1], "grand_total")

    # ------------------------------------------------------------------
    # 7) Aged receivable detail: one row per open document, grouped by
    #    partner.
    # ------------------------------------------------------------------
    def test_aged_receivable_detail(self):
        today = date.today()
        partner = self.Partner.create({"name": "DetailP"})
        due = today - timedelta(days=10)
        move = self.Move.create(
            {
                "journal_id": self.j_misc.id,
                "date": today - timedelta(days=10),
                "invoice_date_due": due,
                "company_id": self.company.id,
                "partner_id": partner.id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": self.acc_recv.id,
                            "name": "detail aging",
                            "debit": 250.0,
                            "credit": 0.0,
                            "partner_id": partner.id,
                            "date_maturity": due,
                        }
                    ),
                    Command.create(
                        {
                            "account_id": self.acc_revenue.id,
                            "name": "detail aging",
                            "debit": 0.0,
                            "credit": 250.0,
                            "partner_id": partner.id,
                        }
                    ),
                ],
            }
        )
        move.action_post()

        ar = self.env["custom.report.aged.receivable"].with_context(aging_detail=True)
        result = ar._build_lines(self._filters())
        self.assertEqual(result.get("type"), "aging_detail")
        grp = next(g for g in result["partners"] if g["partner_name"] == "DetailP")
        self.assertEqual(len(grp["rows"]), 1, "One open document expected.")
        row = grp["rows"][0]
        self.assertEqual(row["doc_no"], move.name)
        self.assertAlmostEqual(row["d_0_30"], 250.0, places=2)
        self.assertAlmostEqual(grp["subtotal"]["total"], 250.0, places=2)

    # ------------------------------------------------------------------
    # 8) Kartu Utang / Kartu Piutang: side-restricted partner cards.
    # ------------------------------------------------------------------
    def test_partner_cards(self):
        today = date.today()
        self._post_move(
            [(self.acc_recv, 700.0, 0.0), (self.acc_revenue, 0.0, 700.0)],
            dt=today,
            partner=self.partner_a,
            ref="AR1",
        )
        self._post_move(
            [(self.acc_expense, 400.0, 0.0), (self.acc_pay, 0.0, 400.0)],
            dt=today,
            partner=self.partner_b,
            ref="AP1",
        )

        rc = self.env["custom.report.receivable.card"]
        r_lines = rc._build_lines(self._filters())
        r_grand = next(l for l in r_lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(r_grand["total_debit"], 700.0, places=2)

        pc = self.env["custom.report.payable.card"]
        p_lines = pc._build_lines(self._filters())
        p_grand = next(l for l in p_lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(p_grand["total_credit"], 400.0, places=2)

    # ------------------------------------------------------------------
    # 9) Uang Muka ledger: auto-detects advance accounts by name.
    # ------------------------------------------------------------------
    def test_advance_report(self):
        today = date.today()
        adv = self._mk_account("11910", "Uang Muka Pembelian", "asset_current")
        self._post_move(
            [(adv, 150.0, 0.0), (self.acc_cash, 0.0, 150.0)],
            dt=today,
            partner=self.partner_a,
            ref="ADV1",
        )

        rep = self.env["custom.report.advance"]
        lines = rep._build_lines(self._filters())  # empty account_ids -> auto-detect
        acct = next(
            (l for l in lines if l.get("type") == "account" and l.get("account_code") == "11910"),
            None,
        )
        self.assertIsNotNone(acct, "Advance account must be auto-detected by name.")
        self.assertAlmostEqual(acct["total_debit"], 150.0, places=2)

    # ------------------------------------------------------------------
    # 10) Sales register: one row per invoice line, totals net of refunds.
    # ------------------------------------------------------------------
    def test_sales_report(self):
        today = date.today()
        inv = self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": self.partner_a.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Service line",
                            "quantity": 2.0,
                            "price_unit": 300.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [],
                        }
                    )
                ],
            }
        )
        inv.action_post()

        rep = self.env["custom.report.sales"]
        lines = rep._build_lines(self._filters())
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["untaxed"], 600.0, places=2)
        self.assertAlmostEqual(grand["quantity"], 2.0, places=2)

    # ------------------------------------------------------------------
    # Tax-team reports: Faktur Pajak rekap + PPN reconciliation.
    # ------------------------------------------------------------------
    def _mk_ppn_tax(self, name, type_tax_use, tax_account, amount=11.0):
        """Create a percentage tax whose tax line posts to ``tax_account``.

        Uses the Odoo-19 ``repartition_line_ids`` + ``document_type`` shape
        (mirrors ``account.tax-id_psak.csv``).
        """
        return self.env["account.tax"].create(
            {
                "name": name,
                "amount": amount,
                "amount_type": "percent",
                "type_tax_use": type_tax_use,
                "company_id": self.company.id,
                "tax_group_id": self.tax_group.id,
                "country_id": self.env.ref("base.id").id,
                "repartition_line_ids": [
                    Command.create({"repartition_type": "base", "document_type": "invoice", "factor_percent": 100.0}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "document_type": "invoice",
                            "factor_percent": 100.0,
                            "account_id": tax_account.id,
                        }
                    ),
                    Command.create({"repartition_type": "base", "document_type": "refund", "factor_percent": 100.0}),
                    Command.create(
                        {
                            "repartition_type": "tax",
                            "document_type": "refund",
                            "factor_percent": 100.0,
                            "account_id": tax_account.id,
                        }
                    ),
                ],
            }
        )

    def test_faktur_pajak_keluaran(self):
        today = date.today()
        acc_ppn_out = self._mk_account("21200", "PPN Keluaran", "liability_current")
        ppn = self._mk_ppn_tax("PPN Keluaran 11%", "sale", acc_ppn_out)
        inv = self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": self.partner_a.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Barang A",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [Command.set([ppn.id])],
                        }
                    )
                ],
            }
        )
        inv.action_post()

        rep = self.env["custom.report.faktur.pajak"]
        lines = rep._build_lines(self._filters(faktur_type="keluaran"))
        rows = [l for l in lines if l.get("type") != "grand_total"]
        self.assertEqual(len(rows), 1, "Exactly one faktur keluaran expected.")
        self.assertAlmostEqual(rows[0]["dpp"], 1000.0, places=2)
        self.assertAlmostEqual(rows[0]["ppn"], 110.0, places=2)
        self.assertEqual(rows[0]["invoice_no"], inv.name)
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["ppn"], 110.0, places=2)
        self.assertAlmostEqual(grand["total"], 1110.0, places=2)

    def test_faktur_pajak_masukan_sign(self):
        """Vendor-side DPP/PPN must come out POSITIVE (sign flip)."""
        today = date.today()
        acc_ppn_in = self._mk_account("11700", "PPN Masukan", "asset_current")
        ppn = self._mk_ppn_tax("PPN Masukan 11%", "purchase", acc_ppn_in)
        j_purchase = self.Journal.create(
            {"name": "Purchases", "code": "BILL", "type": "purchase", "company_id": self.company.id}
        )
        bill = self.Move.create(
            {
                "move_type": "in_invoice",
                "journal_id": j_purchase.id,
                "partner_id": self.partner_b.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Jasa X",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.acc_expense.id,
                            "tax_ids": [Command.set([ppn.id])],
                        }
                    )
                ],
            }
        )
        bill.action_post()

        rep = self.env["custom.report.faktur.pajak"]
        lines = rep._build_lines(self._filters(faktur_type="masukan"))
        rows = [l for l in lines if l.get("type") != "grand_total"]
        self.assertEqual(len(rows), 1, "Exactly one faktur masukan expected.")
        self.assertAlmostEqual(rows[0]["dpp"], 1000.0, places=2)
        self.assertAlmostEqual(rows[0]["ppn"], 110.0, places=2)

    def test_tax_report_reconciliation(self):
        """A manual (non-tax) posting to a PPN account surfaces as selisih."""
        today = date.today()
        acc_ppn_out = self._mk_account("21210", "PPN Keluaran Rec", "liability_current")
        ppn = self._mk_ppn_tax("PPN Out Rec 11%", "sale", acc_ppn_out)
        inv = self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": self.partner_a.id,
                "invoice_date": today,
                "date": today,
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Barang B",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [Command.set([ppn.id])],
                        }
                    )
                ],
            }
        )
        inv.action_post()
        # Manual journal hitting the PPN account WITHOUT the tax mechanism.
        self._post_move(
            [(acc_ppn_out, 50.0, 0.0), (self.acc_cash, 0.0, 50.0)],
            dt=today,
            ref="manual-ppn",
        )

        report = self.env["custom.report.tax"]
        lines = report._build_lines(self._filters())
        recon = [l for l in lines if l.get("type") == "reconciliation"]
        self.assertTrue(recon, "Reconciliation rows must be emitted for PPN accounts.")
        row = next(r for r in recon if "PPN Keluaran Rec" in (r["account"] or ""))
        self.assertAlmostEqual(
            row["selisih"],
            50.0,
            places=2,
            msg="Selisih must equal the manual (non-tax) movement on the PPN account.",
        )
        total = next(l for l in lines if l.get("type") == "reconciliation_total")
        self.assertAlmostEqual(total["selisih"], 50.0, places=2)

        # The category/grand-total invariant must still hold (unchanged by recon).
        category_subtotal = sum(l["tax_subtotal"] for l in lines if l.get("type") == "category")
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(category_subtotal, grand["tax_amount"], places=2)

    def test_bupot_report(self):
        rep = self.env["custom.report.bupot"]
        filters = self._filters(direction="issued", pph_kind="all")

        if "custom.coretax.bukti.potong" not in self.env:
            # Defensive branch: module not installed -> note + zero total.
            lines = rep._build_lines(filters)
            self.assertTrue(
                any(l.get("type") == "note" for l in lines),
                "An informational note is expected when the bupot module is absent.",
            )
            grand = next(l for l in lines if l.get("type") == "grand_total")
            self.assertAlmostEqual(grand.get("pph") or 0.0, 0.0, places=2)
            return

        # Populated branch: seed one issued PPh 23 slip.
        today = date.today()
        self.env["custom.coretax.bukti.potong"].create(
            {
                "no_bupot": "BP-TEST-1",
                "partner_id": self.partner_a.id,
                "jenis_pph": "23",
                "tarif": 2.0,
                "dpp": 1000.0,
                "pph_terpotong": 20.0,
                "tanggal_bupot": today,
                "period_year": today.year,
                "period_month": today.month,
                "source": "issued",
                "state": "confirmed",
            }
        )
        lines = rep._build_lines(filters)
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["pph"], 20.0, places=2)
        self.assertAlmostEqual(grand["dpp"], 1000.0, places=2)
        self.assertTrue(
            any(l.get("type") == "subtotal" for l in lines),
            "A per-jenis subtotal row is expected.",
        )

    # ------------------------------------------------------------------
    # P2 tax-team reports.
    # ------------------------------------------------------------------
    def _post_ppn_invoice(self, move_type, journal, partner, account, tax, price=1000.0):
        move = self.Move.create(
            {
                "move_type": move_type,
                "journal_id": journal.id,
                "partner_id": partner.id,
                "invoice_date": date.today(),
                "date": date.today(),
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Line",
                            "quantity": 1.0,
                            "price_unit": price,
                            "account_id": account.id,
                            "tax_ids": [Command.set([tax.id])],
                        }
                    )
                ],
            }
        )
        move.action_post()
        return move

    def test_spt_ppn_induk(self):
        """Induk net PPN = PPN Keluaran - PPN Masukan dapat dikreditkan."""
        acc_ppn_out = self._mk_account("21270", "PPN Keluaran SPT", "liability_current")
        acc_ppn_in = self._mk_account("11720", "PPN Masukan SPT", "asset_current")
        ppn_out = self._mk_ppn_tax("PPN Keluaran 11%", "sale", acc_ppn_out)
        ppn_in = self._mk_ppn_tax("PPN Masukan 11%", "purchase", acc_ppn_in)
        j_purchase = self.Journal.create(
            {"name": "Purchases SPT", "code": "BLL3", "type": "purchase", "company_id": self.company.id}
        )
        # Keluaran 2000 -> PPN 220 ; Masukan 1000 -> PPN 110 ; net 110.
        self._post_ppn_invoice("out_invoice", self.j_sale, self.partner_a, self.acc_revenue, ppn_out, price=2000.0)
        self._post_ppn_invoice("in_invoice", j_purchase, self.partner_b, self.acc_expense, ppn_in, price=1000.0)

        rep = self.env["custom.report.spt.ppn"]
        lines = rep._build_lines(self._filters())
        by_uraian = {l.get("uraian"): l for l in lines}
        self.assertAlmostEqual(by_uraian["Jumlah PPN Keluaran"]["ppn"], 220.0, places=2)
        self.assertAlmostEqual(by_uraian["Jumlah PPN Masukan dapat dikreditkan"]["ppn"], 110.0, places=2)
        net = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(net["ppn"], 110.0, places=2)

    def test_pph_withholding_report(self):
        rep = self.env["custom.report.pph.withholding"]
        filters = self._filters(pph_kind="all")
        if "account.move.withholding.line" not in self.env:
            lines = rep._build_lines(filters)
            self.assertTrue(any(l.get("type") == "note" for l in lines))
            return
        # Seed a PPh 23 category + rule, then a withholding line on a bill.
        acc_pph = self._mk_account("21290", "Hutang PPh 23", "liability_current")
        cat = self.env["tax.withholding.category"].create({"name": "Jasa", "code": "JASA-TEST", "pph_kind": "pph_23"})
        rule = self.env["tax.withholding.rule"].create(
            {
                "name": "PPh 23 Jasa Test",
                "category_id": cat.id,
                "tarif": 2.0,
                "account_id": acc_pph.id,
                "company_id": self.company.id,
            }
        )
        move = self._post_move(
            [(self.acc_expense, 1000.0, 0.0), (self.acc_pay, 0.0, 1000.0)],
            partner=self.partner_b,
            ref="WHT-BILL",
        )
        self.env["account.move.withholding.line"].create(
            {
                "move_id": move.id,
                "rule_id": rule.id,
                "base_amount": 1000.0,
                "tarif": 2.0,
                "tax_amount": 20.0,
            }
        )
        lines = rep._build_lines(filters)
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["pph"], 20.0, places=2)
        self.assertTrue(
            any(
                l.get("jenis_penghasilan") == "Jasa" for l in lines if l.get("type") not in ("grand_total", "subtotal")
            ),
            "The jenis penghasilan (category) must appear on detail rows.",
        )

    def test_nsfp_monitoring(self):
        acc_ppn_out = self._mk_account("21280", "PPN Keluaran NSFP", "liability_current")
        ppn = self._mk_ppn_tax("PPN Keluaran 11%", "sale", acc_ppn_out)
        self._post_ppn_invoice("out_invoice", self.j_sale, self.partner_a, self.acc_revenue, ppn)

        rep = self.env["custom.report.nsfp.monitoring"]
        lines = rep._build_lines(self._filters())
        Move = self.env["account.move"]
        if "x_custom_coretax_status" not in Move._fields and "x_custom_nsfp" not in Move._fields:
            self.assertTrue(any(l.get("type") == "note" for l in lines))
            return
        detail = [l for l in lines if l.get("type") != "grand_total"]
        self.assertTrue(detail, "NSFP monitoring must list the posted invoice.")
        # Fresh invoice has no NSFP -> flagged.
        self.assertTrue(
            any(l.get("keterangan") == "BELUM ber-NSFP" for l in detail),
            "An invoice without NSFP must be flagged.",
        )
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["ppn"], 110.0, places=2)

    def test_npwp_quality(self):
        rep = self.env["custom.report.npwp.quality"]
        Partner = self.env["res.partner"]
        if "x_custom_npwp_status" not in Partner._fields:
            lines = rep._build_lines(self._filters())
            self.assertTrue(any(l.get("type") == "note" for l in lines))
            return
        # partner_a has no NPWP -> should be flagged; give partner_b a valid one.
        bad = self.Partner.create({"name": "No NPWP Co"})
        good = self.Partner.create({"name": "Good NPWP Co", "x_custom_npwp": "012345678901234"})
        self._post_move(
            [(self.acc_recv, 500.0, 0.0), (self.acc_revenue, 0.0, 500.0)],
            partner=bad,
            ref="NPWP-BAD",
        )
        self._post_move(
            [(self.acc_recv, 500.0, 0.0), (self.acc_revenue, 0.0, 500.0)],
            partner=good,
            ref="NPWP-GOOD",
        )
        # _post_move uses a general journal (move_type entry); the report scans
        # invoice move types, so post via an actual invoice for the "bad" one.
        self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": bad.id,
                "invoice_date": date.today(),
                "date": date.today(),
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "X",
                            "quantity": 1.0,
                            "price_unit": 100.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [],
                        }
                    )
                ],
            }
        ).action_post()
        self.Move.create(
            {
                "move_type": "out_invoice",
                "journal_id": self.j_sale.id,
                "partner_id": good.id,
                "invoice_date": date.today(),
                "date": date.today(),
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Y",
                            "quantity": 1.0,
                            "price_unit": 100.0,
                            "account_id": self.acc_revenue.id,
                            "tax_ids": [],
                        }
                    )
                ],
            }
        ).action_post()

        lines = rep._build_lines(self._filters())
        problem_partners = {l.get("partner") for l in lines if l.get("type") not in ("grand_total", "note")}
        self.assertIn("No NPWP Co", problem_partners, "Partner without NPWP must be flagged.")
        self.assertNotIn("Good NPWP Co", problem_partners, "Partner with valid NPWP must not be flagged.")

    # ------------------------------------------------------------------
    # P3/P4 tax-team reports.
    # ------------------------------------------------------------------
    def test_dpp_nilai_lain(self):
        Tax = self.env["account.tax"]
        rep = self.env["custom.report.dpp.nilai.lain"]
        if "x_custom_dpp_method" not in Tax._fields:
            self.assertTrue(any(l.get("type") == "note" for l in rep._build_lines(self._filters())))
            return
        acc_ppn = self._mk_account("21310", "PPN Keluaran NL", "liability_current")
        tax = self._mk_ppn_tax("PPN 12% DPP Nilai Lain", "sale", acc_ppn, amount=12.0)
        tax.write({"x_custom_dpp_method": "nilai_lain", "x_custom_dpp_factor": 0.916667})
        self._post_ppn_invoice("out_invoice", self.j_sale, self.partner_a, self.acc_revenue, tax, price=1000.0)

        lines = rep._build_lines(self._filters())
        detail = [l for l in lines if l.get("type") != "grand_total"]
        self.assertEqual(len(detail), 1, "The nilai-lain invoice must appear once.")
        row = detail[0]
        self.assertAlmostEqual(row["dpp_penuh"], 1000.0, places=0)
        self.assertAlmostEqual(row["dpp_nilai_lain"], 916.667, places=1)
        self.assertLess(row["dpp_nilai_lain"], row["dpp_penuh"], "DPP nilai lain must be reduced.")
        self.assertGreater(row["ppn"], 0.0)

    def test_faktur_pengganti(self):
        Move = self.env["account.move"]
        rep = self.env["custom.report.faktur.pengganti"]
        has_fields = any(
            f in Move._fields
            for f in (
                "x_custom_coretax_kode_status",
                "x_custom_coretax_status_code",
                "x_custom_coretax_replacement_of_id",
            )
        )
        if not has_fields:
            self.assertTrue(any(l.get("type") == "note" for l in rep._build_lines(self._filters())))
            return
        acc_ppn = self._mk_account("21320", "PPN Keluaran FP", "liability_current")
        tax = self._mk_ppn_tax("PPN Keluaran 11%", "sale", acc_ppn)
        inv = self._post_ppn_invoice("out_invoice", self.j_sale, self.partner_a, self.acc_revenue, tax)
        # Mark as pengganti (kode 01).
        if "x_custom_coretax_kode_status" in Move._fields:
            inv.write({"x_custom_coretax_kode_status": "01"})
        else:
            inv.write({"x_custom_coretax_status_code": "01"})

        lines = rep._build_lines(self._filters())
        detail = [l for l in lines if l.get("type") != "grand_total"]
        self.assertTrue(
            any(r["doc_no"] == inv.name and r["kode"] == "01" for r in detail),
            "The pengganti faktur must be listed with kode 01.",
        )

    def test_ekualisasi_omzet(self):
        acc_ppn = self._mk_account("21330", "PPN Keluaran EQ", "liability_current")
        tax = self._mk_ppn_tax("PPN Keluaran 11%", "sale", acc_ppn)
        self._post_ppn_invoice("out_invoice", self.j_sale, self.partner_a, self.acc_revenue, tax, price=1000.0)

        rep = self.env["custom.report.ekualisasi.omzet"]
        lines = rep._build_lines(self._filters())
        by_uraian = {l.get("uraian"): l["amount"] for l in lines}
        ppn_omzet = next(v for k, v in by_uraian.items() if "SPT Masa PPN" in k)
        gl_omzet = next(v for k, v in by_uraian.items() if "Buku Besar" in k)
        self.assertAlmostEqual(ppn_omzet, 1000.0, places=0)
        self.assertAlmostEqual(gl_omzet, 1000.0, places=0)
        selisih = next(l for l in lines if l.get("type") == "grand_total")["amount"]
        self.assertAlmostEqual(selisih, 0.0, places=0, msg="Omzet PPN and GL should reconcile here.")

    def test_pph_equalisasi(self):
        Template = self.env["product.template"]
        rep = self.env["custom.report.pph.equalisasi"]
        if "x_custom_withholding_category_id" not in Template._fields:
            self.assertTrue(any(l.get("type") == "note" for l in rep._build_lines(self._filters())))
            return
        cat = self.env["tax.withholding.category"].create(
            {"name": "Jasa Konsultan", "code": "JASA-EQ", "pph_kind": "pph_23"}
        )
        product = self.env["product.product"].create(
            {"name": "Jasa Konsultan", "x_custom_withholding_category_id": cat.id}
        )
        j_purchase = self.Journal.create(
            {"name": "Purchases EQ", "code": "BLL4", "type": "purchase", "company_id": self.company.id}
        )
        bill = self.Move.create(
            {
                "move_type": "in_invoice",
                "journal_id": j_purchase.id,
                "partner_id": self.partner_b.id,
                "invoice_date": date.today(),
                "date": date.today(),
                "company_id": self.company.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Jasa",
                            "quantity": 1.0,
                            "price_unit": 1000.0,
                            "product_id": product.id,
                            "account_id": self.acc_expense.id,
                        }
                    )
                ],
            }
        )
        bill.action_post()

        lines = rep._build_lines(self._filters())
        detail = [l for l in lines if l.get("type") not in ("grand_total", "note")]
        row = next((r for r in detail if r["doc_no"] == bill.name), None)
        self.assertIsNotNone(row, "The objek-PPh bill line must be listed.")
        self.assertAlmostEqual(row["dpp"], 1000.0, places=0)
        self.assertIn(row["status"], ("Dipotong", "BELUM dipotong"))

    def test_coretax_submission_query(self):
        """The submission monitor must run without error and emit a total."""
        rep = self.env["custom.report.coretax.submission"]
        lines = rep._build_lines(self._filters())
        self.assertTrue(lines, "Report must always emit at least a total/note row.")
        self.assertTrue(
            any(l.get("type") in ("grand_total", "note") for l in lines),
            "A grand_total (or note when module absent) is expected.",
        )

    def test_pajakku_usage(self):
        rep = self.env["custom.report.pajakku.usage"]
        if "custom.coretax.pajakku.usage" not in self.env:
            self.assertTrue(any(l.get("type") == "note" for l in rep._build_lines(self._filters())))
            return
        self.env["custom.coretax.pajakku.usage"].create(
            {
                "company_id": self.company.id,
                "period": date.today().replace(day=1),
                "api_calls": 10,
                "faktur_submits": 3,
                "bupot_submits": 2,
                "errors": 1,
            }
        )
        lines = rep._build_lines(self._filters())
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["api_calls"], 10, places=0)
        self.assertAlmostEqual(grand["faktur_submits"], 3, places=0)

    # ------------------------------------------------------------------
    # Deferred-batch reports (withholding GL reconciliation, PPh 25, audit).
    # ------------------------------------------------------------------
    def test_pph_reconciliation(self):
        acc_hutang = self._mk_account("21320R", "Hutang PPh 23", "liability_current")
        # Terutang: Cr Hutang PPh 20,000 (as booked by the withholding GL entry).
        self._post_move([(self.acc_expense, 20000.0, 0.0), (acc_hutang, 0.0, 20000.0)], ref="terutang")
        # Disetor: Dr Hutang PPh 15,000 (setoran/NTPN).
        self._post_move([(acc_hutang, 15000.0, 0.0), (self.acc_cash, 0.0, 15000.0)], ref="setor")
        rep = self.env["custom.report.pph.reconciliation"]
        lines = rep._build_lines(self._filters())
        row = next(
            r
            for r in lines
            if r.get("type") not in ("grand_total", "note") and "Hutang PPh 23" in (r.get("account") or "")
        )
        self.assertAlmostEqual(row["terutang"], 20000.0, places=2)
        self.assertAlmostEqual(row["disetor"], 15000.0, places=2)
        self.assertAlmostEqual(row["saldo_akhir"], 5000.0, places=2)

    def test_pph25(self):
        acc25 = self._mk_account("11630R", "PPh 25 Dibayar di Muka", "asset_current")
        self._post_move([(acc25, 5000.0, 0.0), (self.acc_cash, 0.0, 5000.0)], ref="angsuran")
        rep = self.env["custom.report.pph25"]
        lines = rep._build_lines(self._filters())
        detail = [l for l in lines if l.get("type") not in ("grand_total", "note")]
        self.assertTrue(any(r["debit"] == 5000.0 for r in detail), "The PPh 25 installment must be listed.")
        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["debit"], 5000.0, places=2)
        self.assertAlmostEqual(grand["saldo"], 5000.0, places=2)

    def test_tax_audit_runs(self):
        """The audit-trail query must run and always emit a total/note."""
        rep = self.env["custom.report.tax.audit"]
        lines = rep._build_lines(self._filters())
        self.assertTrue(lines)
        self.assertTrue(any(l.get("type") in ("grand_total", "note") for l in lines))

    def test_drill_down_actions(self):
        """Every 'Lihat Transaksi' button returns a valid act_window whose
        domain is accepted by the ORM."""
        cases = [
            ("custom.report.faktur.pajak.wizard", "account.move"),
            ("custom.report.bupot.wizard", "custom.coretax.bukti.potong"),
            ("custom.report.pph.withholding.wizard", "account.move.withholding.line"),
            ("custom.report.nsfp.monitoring.wizard", "account.move"),
            ("custom.report.npwp.quality.wizard", "res.partner"),
            ("custom.report.dpp.nilai.lain.wizard", "account.move.line"),
            ("custom.report.faktur.pengganti.wizard", "account.move"),
            ("custom.report.pph.equalisasi.wizard", "account.move.line"),
            ("custom.report.coretax.submission.wizard", "custom.coretax.transaction"),
            ("custom.report.pajakku.usage.wizard", "custom.coretax.pajakku.usage"),
            ("custom.report.pph.reconciliation.wizard", "account.move.line"),
            ("custom.report.pph25.wizard", "account.move.line"),
            ("custom.report.tax.audit.wizard", "pdp.audit.log"),
        ]
        for wizard_model, expected in cases:
            if expected not in self.env:
                continue  # optional source module not installed in this DB
            wiz = self.env[wizard_model].create({})
            try:
                action = wiz.action_view_source()
            except UserError:
                continue  # guarded when the source module is absent
            self.assertEqual(action.get("type"), "ir.actions.act_window")
            self.assertEqual(action.get("res_model"), expected)
            # The domain must be valid against the ORM.
            self.env[expected].search(action["domain"], limit=1)

        # Summary reports intentionally have no drill-down.
        wiz = self.env["custom.report.ekualisasi.omzet.wizard"].create({})
        with self.assertRaises(UserError):
            wiz.action_view_source()

    # ------------------------------------------------------------------
    # 20) Account-group hierarchy: reports nest by code prefix, not by
    #     account_type. An Indonesian chart types its cost-of-sales
    #     accounts plain ``expense``, so only the prefix separates COGS
    #     from an operating expense.
    # ------------------------------------------------------------------
    def _seed_groups(self):
        Group = self.env["account.group"]

        def group(prefix, name, parent=None):
            return Group.create(
                {
                    "code_prefix_start": prefix,
                    "code_prefix_end": prefix,
                    "name": name,
                    "company_id": self.company.id,
                    "parent_id": parent.id if parent else False,
                }
            )

        g5 = group("5", "Net Sales")
        group("51", "Gross Sales", g5)
        g6 = group("6", "Cost Of Goods Sold")
        group("61", "Hpp Gross", g6)
        g7 = group("7", "Operating Expenses")
        group("72", "General And Administrative Expenses", g7)
        group("78", "Finance Income", g7)

    def _seed_grouped_pl(self):
        """A chart + four postings exercising every bucket the grouping fixes."""
        self._seed_groups()
        sales = self._mk_account("5100000001", "Gross Sales-Textile", "income")
        # Typed ``expense``, not ``expense_direct_cost`` -- the whole point.
        cogs = self._mk_account("6100000001", "COGS-Textile", "expense")
        opex = self._mk_account("7200000001", "Office Rental", "expense")
        # Typed ``income_other``: belongs under Finance Income, not Revenue.
        interest = self._mk_account("7800000001", "Interest Income", "income_other")

        today = date.today()
        self._post_move([(self.acc_cash, 1000.0, 0.0), (sales, 0.0, 1000.0)], dt=today, ref="G1")
        self._post_move([(cogs, 400.0, 0.0), (self.acc_cash, 0.0, 400.0)], dt=today, ref="G2")
        self._post_move([(opex, 100.0, 0.0), (self.acc_cash, 0.0, 100.0)], dt=today, ref="G3")
        self._post_move([(self.acc_cash, 30.0, 0.0), (interest, 0.0, 30.0)], dt=today, ref="G4")
        return sales

    def test_profit_loss_buckets_by_code_prefix(self):
        self._seed_grouped_pl()
        lines = self.env["custom.report.profit.loss"]._build_lines(self._filters())

        def total(label):
            return next(l for l in lines if l.get("label") == label)["signed_balance"]

        self.assertAlmostEqual(total("Total Revenue"), 1000.0, places=2)
        self.assertAlmostEqual(total("Total COGS"), 400.0, places=2)
        self.assertAlmostEqual(total("Gross Profit"), 600.0, places=2)
        self.assertAlmostEqual(total("Total Operating Expenses"), 100.0, places=2)
        self.assertAlmostEqual(total("Operating Profit"), 500.0, places=2)
        # Finance Income sits below Operating Profit, not inside Revenue.
        self.assertAlmostEqual(total("Net Profit / (Loss) Before Tax"), 530.0, places=2)
        self.assertAlmostEqual(total("Net Profit / (Loss)"), 530.0, places=2)

    def test_profit_loss_nests_group_2_headers(self):
        self._seed_grouped_pl()
        lines = self.env["custom.report.profit.loss"]._build_lines(self._filters())
        revenue = next(l for l in lines if l.get("label") == "Revenue")

        self.assertEqual([s["label"] for s in revenue["subgroups"]], ["Gross Sales"])
        # Prefix "51" is rendered the way Finance writes it.
        self.assertEqual(revenue["subgroups"][0]["code"], "5100000000")
        self.assertEqual(
            [a["account_code"] for a in revenue["subgroups"][0]["accounts"]],
            ["5100000001"],
        )
        # ``accounts`` holds only what has no GROUP 2.
        self.assertFalse(revenue["accounts"])

    def test_balance_sheet_current_year_earnings_balances(self):
        self._seed_grouped_pl()
        lines = self.env["custom.report.balance.sheet"]._build_lines(self._filters())

        def total(label):
            return next(l for l in lines if l.get("label") == label)["signed_balance"]

        self.assertAlmostEqual(total("Current Year Earnings"), 530.0, places=2)
        self.assertAlmostEqual(total("Imbalance (should be zero)"), 0.0, places=2)

    def test_reports_fall_back_to_account_type_without_groups(self):
        """No ``account.group`` for this company -> flat, type-bucketed sections."""
        today = date.today()
        self._post_move(
            [(self.acc_recv, 700.0, 0.0), (self.acc_revenue, 0.0, 700.0)],
            dt=today,
            partner=self.partner_a,
            ref="NoGrp",
        )
        lines = self.env["custom.report.profit.loss"]._build_lines(self._filters())
        revenue = next(l for l in lines if l.get("label") == "Revenue")
        self.assertFalse(revenue.get("subgroups"))
        self.assertEqual([a["account_code"] for a in revenue["accounts"]], ["41000"])

    # ------------------------------------------------------------------
    # 21) Profit & Loss by Branch: one column per Operating Unit; untagged
    #     journal items fall back to the head-office column.
    # ------------------------------------------------------------------
    def test_profit_loss_branch_columns_and_residual(self):
        sales = self._seed_grouped_pl()
        # Name the branch plan through the documented hook so the test does not
        # collide with whatever plan the host database already calls its own.
        plan = self.env["account.analytic.plan"].create({"name": "Branch Test Plan"})
        self.env["ir.config_parameter"].sudo().set_param("custom_accounting_reports.branch_plan_name", plan.name)
        store = self.env["account.analytic.account"].create(
            {"name": "Store One", "plan_id": plan.id, "company_id": self.company.id}
        )
        move = self._post_move(
            [(self.acc_cash, 200.0, 0.0), (sales, 0.0, 200.0)],
            dt=date.today(),
            ref="G-branch",
        )
        move.line_ids.filtered(lambda l: l.account_id == sales).analytic_distribution = {str(store.id): 100.0}

        report = self.env["custom.report.profit.loss.branch"]
        self.assertEqual(
            [c["field"] for c in report._xlsx_columns()],
            ["account_code", "account_name", "hq", "ou_%s" % store.id],
        )

        lines = report._build_lines(self._filters())
        revenue = next(l for l in lines if l.get("label") == "Total Revenue")
        self.assertAlmostEqual(revenue["ou_%s" % store.id], 200.0, places=2)
        # The untagged 1_000 sale is reported under the head office.
        self.assertAlmostEqual(revenue["hq"], 1000.0, places=2)

    def test_profit_loss_wizard_opens_branch_variant(self):
        """The by-branch report rides on the P&L wizard — it owns no table."""
        wizard = self.env["custom.report.profit.loss.wizard"].create({})
        action = wizard.action_view_by_branch()
        self.assertEqual(action["tag"], "custom_report_table")
        self.assertEqual(action["params"]["report_code"], "profit_loss_branch")
        # Same filters as the plain P&L view.
        self.assertEqual(action["params"]["options"], wizard._report_options())
        self.assertFalse(
            self.env["custom.report.profit.loss.branch"]._auto,
            "The by-branch report must stay an AbstractModel (no table).",
        )

    # ------------------------------------------------------------------
    # 22) Aged reports: per-document detail is the default, and the
    #     headers name the document and its control account.
    # ------------------------------------------------------------------
    def test_aged_detail_headers(self):
        payable = self.env["custom.report.aged.payable"].with_context(aging_detail=True)
        headers = [c["header"] for c in payable._xlsx_columns()]
        self.assertEqual(headers[1:3], ["Bill Number", "Bill Reference"])
        self.assertEqual(headers[6], "Payable Account")

        receivable = self.env["custom.report.aged.receivable"].with_context(aging_detail=True)
        headers = [c["header"] for c in receivable._xlsx_columns()]
        self.assertEqual(headers[1], "Invoice Number")
        self.assertEqual(headers[6], "Receivable Account")

    def test_aged_wizards_default_to_detail(self):
        for model in (
            "custom.report.aged.payable.wizard",
            "custom.report.aged.receivable.wizard",
        ):
            wizard = self.env[model].create({})
            self.assertEqual(wizard.detail_mode, "detail")
            self.assertTrue(wizard._report_context_extra()["aging_detail"])

    def test_aged_screen_table_renders(self):
        """The on-screen flattener must cope with the aging dict payload."""
        options = {
            "date_from": "1970-01-01",
            "date_to": date.today().isoformat(),
            "company_ids": [self.company.id],
            "posted_only": True,
        }
        table = self.env["custom.report.aged.payable"].get_report_table(options, {"aging_detail": True})
        self.assertTrue(table["columns"])
        self.assertEqual(table["lines"][-1]["type"], "grand_total")
