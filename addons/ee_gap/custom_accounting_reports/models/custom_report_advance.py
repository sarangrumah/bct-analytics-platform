# -*- coding: utf-8 -*-
"""Uang Muka / Down-Payment ledger.

A per-account ledger restricted to the advance / prepayment accounts
(Uang Muka Pembelian / Penjualan), showing every movement with its
applied / outstanding status derived from reconciliation. Lets finance
see which advances are still open and which have been applied to a bill
or invoice.

Account scope comes from the wizard ``account_ids``; when left empty we
auto-detect accounts whose name looks like an advance account
("uang muka", "advance", "prepayment", "down payment").
"""

from datetime import date as date_cls, timedelta

from odoo import models


class CustomReportAdvance(models.AbstractModel):
    _name = "custom.report.advance"
    _inherit = "custom.report.engine"
    _description = "Custom Advance (Uang Muka) Ledger"

    _report_code = "advance"
    _report_title = "Uang Muka / Down Payment"

    # ------------------------------------------------------------------
    # Account scope
    # ------------------------------------------------------------------
    def _advance_account_ids(self, filters):
        if filters.get("account_ids"):
            return list(filters["account_ids"])
        Account = self.env["account.account"]
        domain = [
            "|",
            "|",
            "|",
            ("name", "ilike", "uang muka"),
            ("name", "ilike", "advance"),
            ("name", "ilike", "prepay"),
            ("name", "ilike", "down payment"),
        ]
        if filters.get("company_ids"):
            domain = [("company_ids", "in", filters["company_ids"])] + domain
        return Account.search(domain).ids

    # ------------------------------------------------------------------
    # XLSX columns / body
    # ------------------------------------------------------------------
    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Journal", "field": "journal_code", "kind": "text", "width": 10},
            {"header": "Document No", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Partner", "field": "partner", "kind": "text", "width": 26},
            {"header": "Label", "field": "label", "kind": "text", "width": 30},
            {"header": "Reference", "field": "reference", "kind": "text", "width": 16},
            {"header": "Status", "field": "status", "kind": "text", "width": 14},
            {"header": "Debit", "field": "debit", "kind": "number", "width": 15},
            {"header": "Credit", "field": "credit", "kind": "number", "width": 15},
            {"header": "Residual", "field": "residual", "kind": "number", "width": 15},
            {"header": "Balance", "field": "running_balance", "kind": "number", "width": 15},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        ncol = len(columns)
        idx = {col["field"]: i for i, col in enumerate(columns)}
        debit_i = idx["debit"]
        currency = ctx.get("currency")
        row = start_row

        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col["header"], fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1

        def _write_totals(line, fmt_num, fmt_text):
            for col_idx, col in enumerate(columns):
                if col_idx < debit_i:
                    continue
                field = col["field"]
                if field == "debit":
                    sheet.write_number(row, col_idx, float(line.get("total_debit") or 0.0), fmt_num)
                elif field == "credit":
                    sheet.write_number(row, col_idx, float(line.get("total_credit") or 0.0), fmt_num)
                elif field == "residual":
                    sheet.write_number(row, col_idx, float(line.get("total_residual") or 0.0), fmt_num)
                elif field == "running_balance":
                    sheet.write_number(row, col_idx, float(line.get("closing") or 0.0), fmt_num)
                elif col["kind"] == "number":
                    sheet.write_number(row, col_idx, 0.0, fmt_num)
                else:
                    sheet.write(row, col_idx, "", fmt_text)

        for line in ctx.get("lines", []):
            ltype = line.get("type")
            if ltype == "account":
                heading = "[%s] %s  —  Opening: %s" % (
                    line.get("account_code") or "",
                    line.get("account_name") or "",
                    self._format_amount(line.get("opening") or 0.0, currency),
                )
                sheet.merge_range(row, 0, row, ncol - 1, heading, fmts["group_text"])
                row += 1
                for ml in line.get("lines", []):
                    for col_idx, col in enumerate(columns):
                        val = ml.get(col["field"])
                        if col["kind"] == "number":
                            sheet.write_number(row, col_idx, float(val or 0.0), fmts["num"])
                        elif col["kind"] == "date":
                            sheet.write(row, col_idx, self._format_date_id(val), fmts["text"])
                        else:
                            sheet.write(row, col_idx, val or "", fmts["text"])
                    row += 1
                sheet.merge_range(
                    row,
                    0,
                    row,
                    debit_i - 1,
                    "Total %s" % (line.get("account_code") or ""),
                    fmts["total_text"],
                )
                _write_totals(line, fmts["total_num"], fmts["total_text"])
                row += 1
            elif ltype == "grand_total":
                sheet.merge_range(row, 0, row, debit_i - 1, line.get("label") or "Grand Total", fmts["total_text"])
                _write_totals(line, fmts["total_num"], fmts["total_text"])
                row += 1
        return row

    # ------------------------------------------------------------------
    # On-screen table
    # ------------------------------------------------------------------
    def _flatten_for_screen(self, lines, columns):
        """Account sections carry their movements in a nested ``lines``
        key — see :py:meth:`custom.report.engine._flatten_grouped`. The
        opening figure rides the Balance column here rather than the
        heading text the Excel body merges it into."""
        return self._flatten_grouped(
            lines,
            columns,
            "account",
            lambda line: "[%s] %s" % (line.get("account_code") or "", line.get("account_name") or ""),
            {
                "debit": "total_debit",
                "credit": "total_credit",
                "residual": "total_residual",
                "running_balance": "closing",
            },
            total_label=lambda line: self.env._("Total %s", line.get("account_code") or ""),
            opening_field="running_balance",
        )

    # ------------------------------------------------------------------
    # Status helper
    # ------------------------------------------------------------------
    def _line_status(self, line):
        account = line.account_id
        if not account.reconcile:
            return ""
        if line.reconciled:
            return "Applied"
        if line.matched_debit_ids or line.matched_credit_ids:
            return "Partial"
        return "Outstanding"

    # ------------------------------------------------------------------
    # Line builder
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        account_ids = self._advance_account_ids(filters)
        if not account_ids:
            return [
                {
                    "type": "grand_total",
                    "label": "No advance accounts found",
                    "total_debit": 0.0,
                    "total_credit": 0.0,
                    "total_residual": 0.0,
                    "closing": 0.0,
                }
            ]

        scoped = dict(filters, account_ids=account_ids)

        # Opening balance per account (before the period).
        opening_filters = dict(
            scoped,
            date_from=date_cls(1970, 1, 1),
            date_to=filters["date_from"] - timedelta(days=1),
        )
        opening_by_account = {aid: row["balance"] for aid, row in self._get_account_balances(opening_filters).items()}

        AML = self.env["account.move.line"]
        period_domain = self._base_move_line_domain(scoped)
        period_lines = AML.search(period_domain, order="account_id, date, id")

        by_account = {}
        for ml in period_lines:
            aid = ml.account_id.id
            bucket = by_account.setdefault(
                aid,
                {
                    "type": "account",
                    "account_id": aid,
                    "account_code": self._account_code(ml.account_id),
                    "account_name": ml.account_id.name or "",
                    "opening": opening_by_account.get(aid, 0.0),
                    "lines": [],
                    "total_debit": 0.0,
                    "total_credit": 0.0,
                    "total_residual": 0.0,
                },
            )
            bucket["total_debit"] += ml.debit
            bucket["total_credit"] += ml.credit
            bucket["total_residual"] += ml.amount_residual
            running = bucket["opening"] + bucket["total_debit"] - bucket["total_credit"]
            bucket["lines"].append(
                {
                    "date": ml.date,
                    "journal_code": ml.journal_id.code or "",
                    "doc_no": ml.move_id.name or "",
                    "partner": ml.partner_id.display_name or "",
                    "label": ml.name or "",
                    "reference": ml.move_id.ref or ml.ref or "",
                    "status": self._line_status(ml),
                    "debit": ml.debit,
                    "credit": ml.credit,
                    "residual": ml.amount_residual,
                    "running_balance": running,
                }
            )

        # Accounts with opening but no movement.
        for aid, opening in opening_by_account.items():
            if aid in by_account or not opening:
                continue
            account = self.env["account.account"].browse(aid)
            by_account[aid] = {
                "type": "account",
                "account_id": aid,
                "account_code": self._account_code(account),
                "account_name": account.name or "",
                "opening": opening,
                "lines": [],
                "total_debit": 0.0,
                "total_credit": 0.0,
                "total_residual": 0.0,
            }

        lines = []
        g_d = g_c = g_r = g_cl = 0.0
        for aid in sorted(by_account, key=lambda a: by_account[a]["account_code"] or ""):
            bucket = by_account[aid]
            bucket["closing"] = bucket["opening"] + bucket["total_debit"] - bucket["total_credit"]
            lines.append(bucket)
            g_d += bucket["total_debit"]
            g_c += bucket["total_credit"]
            g_r += bucket["total_residual"]
            g_cl += bucket["closing"]

        lines.append(
            {
                "type": "grand_total",
                "label": "Grand Total",
                "total_debit": g_d,
                "total_credit": g_c,
                "total_residual": g_r,
                "closing": g_cl,
            }
        )
        return lines
