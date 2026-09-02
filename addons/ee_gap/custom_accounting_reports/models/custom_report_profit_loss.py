# -*- coding: utf-8 -*-
"""Profit & Loss down to Total Comprehensive Income.

Sections follow the chart-of-accounts hierarchy (``account.group``), not
``account_type``. Indonesian charts put every cost-of-sales account under
prefix ``6`` while typing them plain ``expense``, so an ``account_type``
split reports a zero COGS and files finance income under Revenue. Grouping
by account-code prefix is what Finance reconciles against. Databases with no
``account.group`` fall back to ``account_type``.

Every amount is shown with its natural sign: revenue-nature accounts are
flipped positive, cost-nature accounts stay positive. The running-profit
lines therefore add income buckets and subtract cost buckets explicitly
rather than summing signed balances.
"""

from datetime import date as date_cls

from odoo import models


INCOME_TYPES = ("income", "income_other")

# GROUP 1 prefix → coarse bucket.
G1_REVENUE = "5"
G1_COGS = "6"
G1_OPEX = "7"
G1_TAX = "8"

# GROUP 2 prefixes that break out of their GROUP 1 default bucket.
G2_OTHER_INCOME = ("76", "78")  # other operating income, finance income
G2_OTHER_EXPENSE = ("77", "79")  # other operating expenses, finance costs
G2_OCI = ("83",)  # other comprehensive income

# Buckets, in report order. ``income`` flags how the bucket enters the
# running-profit arithmetic.
BUCKETS = (
    ("revenue", "Revenue", True),
    ("cogs", "Cost of Goods Sold", False),
    ("opex", "Operating Expenses", False),
    ("other_income", "Other Income", True),
    ("other_expense", "Other Expenses", False),
    ("tax", "Income Tax", False),
    ("oci", "Other Comprehensive Income", True),
)


class CustomReportProfitLoss(models.AbstractModel):
    _name = "custom.report.profit.loss"
    _inherit = "custom.report.engine"
    _description = "Custom Profit & Loss"

    _report_code = "profit_loss"
    _report_title = "Profit & Loss"

    def _xlsx_columns(self):
        return [
            {"header": "Code", "field": "account_code", "kind": "text", "width": 16},
            {"header": "Account", "field": "account_name", "kind": "text", "width": 46},
            {"header": "Period", "field": "signed_balance", "kind": "number", "width": 20},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        return self._xlsx_sectioned_body(
            sheet,
            ctx,
            fmts,
            start_row,
            amount_header="Period",
            secondary=("YTD", "ytd"),
            section_heading=True,
        )

    def _flatten_for_screen(self, lines, columns):
        return self._flatten_sectioned(lines, columns, section_heading=True)

    # ------------------------------------------------------------------
    # Bucketing
    # ------------------------------------------------------------------
    def _pl_buckets(self):
        """``(key, label, is_income)`` in report order — shared with the
        by-branch variant."""
        return BUCKETS

    def _signed(self, row):
        """Revenue-nature accounts carry a credit balance; show them positive."""
        balance = row["balance"]
        return -balance if row["account_type"] in INCOME_TYPES else balance

    def _bucket_key(self, row, group):
        if not group:
            return self._bucket_key_by_type(row)
        g1, g2 = group["g1_code"], group["g2_code"]
        if g1 == G1_REVENUE:
            return "revenue"
        if g1 == G1_COGS:
            return "cogs"
        if g1 == G1_OPEX:
            if g2 in G2_OTHER_INCOME:
                return "other_income"
            if g2 in G2_OTHER_EXPENSE:
                return "other_expense"
            if not g2:
                # GROUP 1 "7" with no GROUP 2 (e.g. share of net income from
                # associates) — an income account there is not an expense.
                return "other_income" if row["account_type"] in INCOME_TYPES else "opex"
            return "opex"
        if g1 == G1_TAX:
            return "oci" if g2 in G2_OCI else "tax"
        return None

    def _bucket_key_by_type(self, row):
        """Best-effort bucket for charts that carry no ``account.group``."""
        return {
            "income": "revenue",
            "income_other": "other_income",
            "expense_direct_cost": "cogs",
            "expense": "opex",
            "expense_depreciation": "other_expense",
            "expense_other": "other_expense",
        }.get(row["account_type"])

    def _bucket_rows(self, balances):
        """``{bucket_key: [row with signed_balance, ...]}`` for every P&L account."""
        groups = self._account_groups(list(balances))
        buckets = {key: [] for key, _label, _income in BUCKETS}
        for row in balances.values():
            key = self._bucket_key(row, groups.get(row["account_id"]))
            if key:
                buckets[key].append(dict(row, signed_balance=self._signed(row)))
        return buckets

    def _ytd_period(self, filters):
        """Year-to-Date period: from 1-Jan of date_to's year until
        date_to. Returns ``{account_id: balance}``.
        """
        ytd_start = date_cls(filters["date_to"].year, 1, 1)
        ytd_filters = dict(filters, date_from=ytd_start)
        return self._get_account_balances(ytd_filters)

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        period = self._bucket_rows(self._get_account_balances(filters))
        ytd = self._bucket_rows(self._ytd_period(filters))

        def totals(source):
            return {key: sum(row["signed_balance"] for row in source[key]) for key, _label, _income in BUCKETS}

        p, y = totals(period), totals(ytd)

        def milestones(t):
            gross = t["revenue"] - t["cogs"]
            operating = gross - t["opex"]
            before_tax = operating + t["other_income"] - t["other_expense"]
            after_tax = before_tax - t["tax"]
            return gross, operating, before_tax, after_tax, after_tax + t["oci"]

        p_gross, p_op, p_before, p_after, p_comp = milestones(p)
        y_gross, y_op, y_before, y_after, y_comp = milestones(y)

        labels = {key: label for key, label, _income in BUCKETS}

        def section(key):
            """Emit a section only when it holds accounts — an empty
            "Other Comprehensive Income" heading helps nobody."""
            return self._grouped_section(labels[key], period[key]) if period[key] else None

        def total(label, value, ytd_value, ltype="total"):
            return {"type": ltype, "label": label, "signed_balance": value, "ytd": ytd_value}

        lines = [
            section("revenue"),
            total("Total Revenue", p["revenue"], y["revenue"]),
            section("cogs"),
            total("Total COGS", p["cogs"], y["cogs"]),
            total("Gross Profit", p_gross, y_gross),
            section("opex"),
            total("Total Operating Expenses", p["opex"], y["opex"]),
            total("Operating Profit", p_op, y_op),
            section("other_income"),
            section("other_expense"),
            total("Net Profit / (Loss) Before Tax", p_before, y_before),
            section("tax"),
            total("Net Profit / (Loss)", p_after, y_after, ltype="grand_total"),
        ]
        if period["oci"]:
            lines.append(section("oci"))
            lines.append(total("Total Comprehensive Income", p_comp, y_comp, ltype="grand_total"))
        return [line for line in lines if line]
