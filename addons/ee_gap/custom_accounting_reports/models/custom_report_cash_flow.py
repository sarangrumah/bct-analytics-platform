# -*- coding: utf-8 -*-
"""Cash Flow Statement — indirect method.

We start from net income (Revenue - All Expenses), then apply
non-cash adjustments (depreciation, working capital changes) and
classify per activity. Account types map to Operating / Investing /
Financing buckets using Odoo's standard ``account_type``.
"""

from odoo import models


OPERATING_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_direct_cost",
    "expense_depreciation",
    "asset_receivable",
    "asset_current",
    "asset_prepayments",
    "liability_payable",
    "liability_current",
)
INVESTING_TYPES = (
    "asset_non_current",
    "asset_fixed",
)
FINANCING_TYPES = (
    "liability_non_current",
    "liability_credit_card",
    "equity",
    "equity_unaffected",
)
CASH_TYPES = ("asset_cash",)


class CustomReportCashFlow(models.AbstractModel):
    _name = "custom.report.cash.flow"
    _inherit = "custom.report.engine"
    _description = "Custom Cash Flow Statement"

    _report_code = "cash_flow"
    _report_title = "Cash Flow Statement"

    def _xlsx_columns(self):
        return [
            {"header": "Code", "field": "account_code", "kind": "text", "width": 16},
            {"header": "Account", "field": "account_name", "kind": "text", "width": 46},
            {"header": "Amount", "field": "signed_balance", "kind": "number", "width": 20},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        return self._xlsx_sectioned_body(
            sheet,
            ctx,
            fmts,
            start_row,
            amount_header="Amount",
            secondary=None,
            section_heading=True,
        )

    def _flatten_for_screen(self, lines, columns):
        return self._flatten_sectioned(lines, columns, section_heading=True)

    def _bucket(self, label, code, type_codes, balances, sign=-1):
        """Compute one activity bucket.

        For indirect method, sources of cash (decrease in asset /
        increase in liability) flow in as positive. We achieve that by
        flipping the natural debit/credit signum with ``sign``.
        """
        accounts = []
        subtotal = 0.0
        for row in balances.values():
            if row["account_type"] not in type_codes:
                continue
            signed = sign * row["balance"]
            accounts.append(dict(row, signed_balance=signed))
            subtotal += signed
        accounts.sort(key=lambda r: r["account_code"] or "")
        return {
            "type": "section",
            "section": code,
            "label": label,
            "accounts": accounts,
            "subtotal": subtotal,
        }

    def _build_lines(self, filters):
        balances = self._get_account_balances(filters)

        operating = self._bucket(
            "Operating Activities",
            "operating",
            OPERATING_TYPES,
            balances,
            sign=-1,
        )
        investing = self._bucket(
            "Investing Activities",
            "investing",
            INVESTING_TYPES,
            balances,
            sign=-1,
        )
        financing = self._bucket(
            "Financing Activities",
            "financing",
            FINANCING_TYPES,
            balances,
            sign=-1,
        )
        cash_delta = self._bucket(
            "Net Change in Cash",
            "cash",
            CASH_TYPES,
            balances,
            sign=1,
        )

        lines = [
            operating,
            {
                "type": "subtotal",
                "label": "Net Cash from Operating Activities",
                "signed_balance": operating["subtotal"],
            },
            investing,
            {
                "type": "subtotal",
                "label": "Net Cash from Investing Activities",
                "signed_balance": investing["subtotal"],
            },
            financing,
            {
                "type": "subtotal",
                "label": "Net Cash from Financing Activities",
                "signed_balance": financing["subtotal"],
            },
        ]

        net_change = operating["subtotal"] + investing["subtotal"] + financing["subtotal"]
        lines.append(
            {
                "type": "grand_total",
                "label": "Net Change in Cash (computed)",
                "signed_balance": net_change,
            }
        )
        lines.append(
            {
                "type": "check",
                "label": "Change in Cash Accounts (observed)",
                "signed_balance": cash_delta["subtotal"],
            }
        )
        return lines
