# -*- coding: utf-8 -*-
"""Balance Sheet: Asset / Liability / Equity, nested by account group.

Uses Odoo 19 native ``account.account.account_type`` to pick the three
statements, and ``account.group`` (GROUP 2) to nest accounts underneath —
so an Indonesian chart reads "Assets → Current Assets → 1102000001 Cash on
hand IDR" exactly as Finance maps it.

Income and expense accounts never appear on a balance sheet, but their net
balance is real equity: it is reported as a computed "Current Year Earnings"
line. Without it the statement is short by exactly the period's net result.
"""

from datetime import date as date_cls

from odoo import models


ASSET_TYPES = (
    "asset_receivable",
    "asset_cash",
    "asset_current",
    "asset_non_current",
    "asset_prepayments",
    "asset_fixed",
)
LIABILITY_TYPES = (
    "liability_payable",
    "liability_credit_card",
    "liability_current",
    "liability_non_current",
)
EQUITY_TYPES = (
    "equity",
    "equity_unaffected",
)
# Accounts whose cumulative balance rolls up into Current Year Earnings.
EARNINGS_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_direct_cost",
    "expense_depreciation",
    "expense_other",
)


class CustomReportBalanceSheet(models.AbstractModel):
    _name = "custom.report.balance.sheet"
    _inherit = "custom.report.engine"
    _description = "Custom Balance Sheet"

    _report_code = "balance_sheet"
    _report_title = "Balance Sheet"

    def _xlsx_columns(self):
        return [
            {"header": "Code", "field": "account_code", "kind": "text", "width": 16},
            {"header": "Account", "field": "account_name", "kind": "text", "width": 46},
            {"header": "Balance", "field": "signed_balance", "kind": "number", "width": 20},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        has_comp = any(line.get("comparison") is not None for line in ctx.get("lines", []))
        secondary = ("Prior Period", "comparison") if has_comp else None
        return self._xlsx_sectioned_body(
            sheet,
            ctx,
            fmts,
            start_row,
            amount_header="Balance",
            secondary=secondary,
            section_heading=False,
        )

    def _flatten_for_screen(self, lines, columns):
        return self._flatten_sectioned(lines, columns, section_heading=False)

    def _default_filters(self):
        filters = super()._default_filters()
        # Balance Sheet is cumulative — pin date_from far back.
        filters["date_from"] = date_cls(1970, 1, 1)
        return filters

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------
    def _signed_rows(self, type_codes, balances, flip_sign):
        rows = []
        for row in balances.values():
            if row["account_type"] not in type_codes:
                continue
            signed = -row["balance"] if flip_sign else row["balance"]
            rows.append(dict(row, signed_balance=signed))
        return rows

    def _subtotal(self, type_codes, balances, flip_sign):
        return sum(row["signed_balance"] for row in self._signed_rows(type_codes, balances, flip_sign))

    def _current_year_earnings(self, balances):
        """Net result of every income/expense account, as an equity credit."""
        return -sum(row["balance"] for row in balances.values() if row["account_type"] in EARNINGS_TYPES)

    def _section(self, label, type_codes, balances, flip_sign):
        """Kept for backwards compatibility with overriding modules."""
        rows = self._signed_rows(type_codes, balances, flip_sign)
        rows.sort(key=lambda r: r["account_code"] or "")
        return rows, sum(r["signed_balance"] for r in rows)

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        # Always cumulative; ignore caller's date_from.
        filters = dict(filters)
        filters["date_from"] = date_cls(1970, 1, 1)

        balances = self._get_account_balances(filters)

        # Comparison period (prior year same date_to) — optional.
        comparison = {}
        if filters.get("comparison_date_to"):
            comp_filters = dict(
                filters,
                date_from=date_cls(1970, 1, 1),
                date_to=filters["comparison_date_to"],
            )
            comparison = self._get_account_balances(comp_filters)

        def comp(type_codes, flip_sign, add_earnings=False):
            if not comparison:
                return None
            value = self._subtotal(type_codes, comparison, flip_sign)
            if add_earnings:
                value += self._current_year_earnings(comparison)
            return value

        total_assets = self._subtotal(ASSET_TYPES, balances, False)
        total_liab = self._subtotal(LIABILITY_TYPES, balances, True)
        earnings = self._current_year_earnings(balances)
        equity_rows = self._signed_rows(EQUITY_TYPES, balances, True)
        total_equity = sum(row["signed_balance"] for row in equity_rows) + earnings

        lines = [
            {"type": "header", "label": "ASSETS"},
            self._grouped_section("Assets", self._signed_rows(ASSET_TYPES, balances, False)),
            {
                "type": "total",
                "label": "Total Assets",
                "signed_balance": total_assets,
                "comparison": comp(ASSET_TYPES, False),
            },
            {"type": "header", "label": "LIABILITIES"},
            self._grouped_section("Liabilities", self._signed_rows(LIABILITY_TYPES, balances, True)),
            {
                "type": "total",
                "label": "Total Liabilities",
                "signed_balance": total_liab,
                "comparison": comp(LIABILITY_TYPES, True),
            },
            {"type": "header", "label": "EQUITY"},
            self._grouped_section("Equity", equity_rows),
            {
                "type": "subtotal",
                "label": "Current Year Earnings",
                "signed_balance": earnings,
            },
            {
                "type": "total",
                "label": "Total Equity",
                "signed_balance": total_equity,
                "comparison": comp(EQUITY_TYPES, True, add_earnings=True),
            },
            {
                "type": "grand_total",
                "label": "Total Liabilities + Equity",
                "signed_balance": total_liab + total_equity,
            },
            {
                "type": "check",
                "label": "Imbalance (should be zero)",
                "signed_balance": total_assets - (total_liab + total_equity),
            },
        ]
        return lines
