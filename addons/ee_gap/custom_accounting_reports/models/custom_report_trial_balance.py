# -*- coding: utf-8 -*-
"""Trial Balance: opening + movement + closing per account."""

from datetime import date as date_cls, timedelta

from odoo import models


class CustomReportTrialBalance(models.AbstractModel):
    _name = "custom.report.trial.balance"
    _inherit = "custom.report.engine"
    _description = "Custom Trial Balance"

    _report_code = "trial_balance"
    _report_title = "Trial Balance"

    def _xlsx_columns(self):
        return [
            {"header": "Code", "field": "account_code", "kind": "text", "width": 14},
            {"header": "Account", "field": "account_name", "kind": "text", "width": 42},
            # ``drilldown`` is screen-only (the Excel writer ignores unknown
            # keys): clicking an opening cell opens the GL for the stretch of
            # the fiscal year that precedes this report's period.
            {
                "header": "Opening Debit",
                "field": "opening_debit",
                "kind": "number",
                "width": 16,
                "drilldown": "opening",
            },
            {
                "header": "Opening Credit",
                "field": "opening_credit",
                "kind": "number",
                "width": 16,
                "drilldown": "opening",
            },
            {"header": "Movement Debit", "field": "movement_debit", "kind": "number", "width": 16},
            {"header": "Movement Credit", "field": "movement_credit", "kind": "number", "width": 16},
            {"header": "Closing Debit", "field": "closing_debit", "kind": "number", "width": 16},
            {"header": "Closing Credit", "field": "closing_credit", "kind": "number", "width": 16},
        ]

    def _build_lines(self, filters):
        # Opening: everything before date_from.
        opening_filters = dict(
            filters,
            date_from=date_cls(1970, 1, 1),
            date_to=filters["date_from"] - timedelta(days=1),
        )
        opening = self._get_account_balances(opening_filters)
        period = self._get_account_balances(filters)

        all_ids = sorted(set(opening.keys()) | set(period.keys()))
        accounts = self.env["account.account"].browse(all_ids)
        # Re-fetch by code order to keep deterministic display.
        account_index = {a.id: a for a in accounts}

        lines = []
        total_op_d = total_op_c = 0.0
        total_d = total_c = 0.0
        total_cl_d = total_cl_c = 0.0

        for acc in sorted(accounts, key=lambda a: self._account_code(a)):
            o = opening.get(acc.id, {})
            p = period.get(acc.id, {})
            opening_balance = o.get("balance", 0.0)
            opening_debit = opening_balance if opening_balance > 0 else 0.0
            opening_credit = -opening_balance if opening_balance < 0 else 0.0

            movement_debit = p.get("debit", 0.0)
            movement_credit = p.get("credit", 0.0)
            closing_balance = opening_balance + movement_debit - movement_credit
            closing_debit = closing_balance if closing_balance > 0 else 0.0
            closing_credit = -closing_balance if closing_balance < 0 else 0.0

            if not any(
                [
                    opening_debit,
                    opening_credit,
                    movement_debit,
                    movement_credit,
                    closing_debit,
                    closing_credit,
                ]
            ):
                continue

            lines.append(
                {
                    "type": "account",
                    "account_id": acc.id,
                    "account_code": self._account_code(acc),
                    "account_name": acc.name,
                    "account_type": acc.account_type,
                    "opening_debit": opening_debit,
                    "opening_credit": opening_credit,
                    "movement_debit": movement_debit,
                    "movement_credit": movement_credit,
                    "closing_debit": closing_debit,
                    "closing_credit": closing_credit,
                }
            )
            total_op_d += opening_debit
            total_op_c += opening_credit
            total_d += movement_debit
            total_c += movement_credit
            total_cl_d += closing_debit
            total_cl_c += closing_credit

        lines.append(
            {
                "type": "grand_total",
                "label": "Grand Total",
                "opening_debit": total_op_d,
                "opening_credit": total_op_c,
                "movement_debit": total_d,
                "movement_credit": total_c,
                "closing_debit": total_cl_d,
                "closing_credit": total_cl_c,
            }
        )
        return lines
