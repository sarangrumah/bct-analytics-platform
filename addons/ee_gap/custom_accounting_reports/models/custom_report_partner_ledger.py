# -*- coding: utf-8 -*-
"""Partner Ledger: GL grouped by partner first, then chronological."""

from datetime import date as date_cls, timedelta

from odoo import models


class CustomReportPartnerLedger(models.AbstractModel):
    _name = "custom.report.partner.ledger"
    _inherit = "custom.report.engine"
    _description = "Custom Partner Ledger"

    _report_code = "partner_ledger"
    _report_title = "Partner Ledger"

    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "text", "width": 12},
            {"header": "Entry", "field": "move_name", "kind": "text", "width": 18},
            {"header": "Account", "field": "account_code", "kind": "text", "width": 14},
            {"header": "Label", "field": "label", "kind": "text", "width": 34},
            {"header": "Debit", "field": "debit", "kind": "number", "width": 16},
            {"header": "Credit", "field": "credit", "kind": "number", "width": 16},
            {"header": "Balance", "field": "running_balance", "kind": "number", "width": 16},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        ncol = len(columns)
        row = start_row
        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col["header"], fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1

        for line in ctx.get("lines", []):
            ltype = line.get("type")
            if ltype == "partner":
                sheet.merge_range(
                    row,
                    0,
                    row,
                    ncol - 2,
                    line.get("partner_name") or "",
                    fmts["group_text"],
                )
                sheet.write_number(row, ncol - 1, float(line.get("opening") or 0.0), fmts["group_num"])
                row += 1
                for ml in line.get("lines", []):
                    sheet.write(row, 0, self._format_date_id(ml.get("date")), fmts["text"])
                    sheet.write(row, 1, ml.get("move_name") or "", fmts["text"])
                    sheet.write(row, 2, ml.get("account_code") or "", fmts["text"])
                    sheet.write(row, 3, ml.get("label") or "", fmts["text"])
                    sheet.write_number(row, 4, float(ml.get("debit") or 0.0), fmts["num"])
                    sheet.write_number(row, 5, float(ml.get("credit") or 0.0), fmts["num"])
                    sheet.write_number(row, 6, float(ml.get("running_balance") or 0.0), fmts["num"])
                    row += 1
                sheet.merge_range(row, 0, row, 3, "Total", fmts["total_text"])
                sheet.write_number(row, 4, float(line.get("total_debit") or 0.0), fmts["total_num"])
                sheet.write_number(row, 5, float(line.get("total_credit") or 0.0), fmts["total_num"])
                sheet.write_number(row, 6, float(line.get("closing") or 0.0), fmts["total_num"])
                row += 1
            elif ltype == "grand_total":
                sheet.merge_range(row, 0, row, 3, line.get("label") or "Grand Total", fmts["total_text"])
                sheet.write_number(row, 4, float(line.get("total_debit") or 0.0), fmts["total_num"])
                sheet.write_number(row, 5, float(line.get("total_credit") or 0.0), fmts["total_num"])
                sheet.write_number(row, 6, float(line.get("closing") or 0.0), fmts["total_num"])
                row += 1
        return row

    def _flatten_for_screen(self, lines, columns):
        """Partner sections carry their movements in a nested ``lines``
        key — see :py:meth:`custom.report.engine._flatten_grouped`."""
        return self._flatten_grouped(
            lines,
            columns,
            "partner",
            lambda line: line.get("partner_name") or "",
            {"debit": "total_debit", "credit": "total_credit", "running_balance": "closing"},
            opening_field="running_balance",
        )

    def _account_types(self, kind):
        if kind == "receivable":
            return ("asset_receivable",)
        if kind == "payable":
            return ("liability_payable",)
        return ("asset_receivable", "liability_payable")

    def _build_lines(self, filters):
        kind = filters.get("partner_kind") or "both"
        types = list(self._account_types(kind))

        # Opening per partner
        opening_filters = dict(
            filters,
            date_from=date_cls(1970, 1, 1),
            date_to=filters["date_from"] - timedelta(days=1),
        )
        AML = self.env["account.move.line"]
        opening_rows = AML._read_group(
            domain=self._base_move_line_domain(opening_filters)
            + [
                ("account_id.account_type", "in", types),
            ],
            groupby=["partner_id"],
            aggregates=["debit:sum", "credit:sum"],
        )
        opening_by_partner = {(p.id if p else 0): (d or 0.0) - (c or 0.0) for p, d, c in opening_rows}

        period_domain = self._base_move_line_domain(filters) + [
            ("account_id.account_type", "in", types),
        ]
        if filters.get("partner_ids"):
            period_domain.append(("partner_id", "in", filters["partner_ids"]))
        period_lines = AML.search(
            period_domain,
            order="partner_id, date, id",
        )

        partners = {}
        for ml in period_lines:
            pid = ml.partner_id.id or 0
            entry = partners.setdefault(
                pid,
                {
                    "type": "partner",
                    "partner_id": pid,
                    "partner_name": (ml.partner_id.display_name or "— No Partner —"),
                    "opening": opening_by_partner.get(pid, 0.0),
                    "lines": [],
                    "total_debit": 0.0,
                    "total_credit": 0.0,
                },
            )
            entry["total_debit"] += ml.debit
            entry["total_credit"] += ml.credit
            running = entry["opening"] + entry["total_debit"] - entry["total_credit"]
            entry["lines"].append(
                {
                    "date": ml.date,
                    "move_name": ml.move_id.name or ml.move_id.display_name,
                    "account_code": self._account_code(ml.account_id),
                    "label": ml.name or "",
                    "debit": ml.debit,
                    "credit": ml.credit,
                    "running_balance": running,
                }
            )

        # Include partners with only opening movements.
        for pid, opening in opening_by_partner.items():
            if pid in partners or not opening:
                continue
            partner = self.env["res.partner"].browse(pid) if pid else None
            partners[pid] = {
                "type": "partner",
                "partner_id": pid,
                "partner_name": (partner.display_name if partner else "— No Partner —"),
                "opening": opening,
                "lines": [],
                "total_debit": 0.0,
                "total_credit": 0.0,
            }

        lines = []
        total_op = total_d = total_c = total_cl = 0.0
        for entry in sorted(
            partners.values(),
            key=lambda r: r["partner_name"],
        ):
            closing = entry["opening"] + entry["total_debit"] - entry["total_credit"]
            entry["closing"] = closing
            lines.append(entry)
            total_op += entry["opening"]
            total_d += entry["total_debit"]
            total_c += entry["total_credit"]
            total_cl += closing

        lines.append(
            {
                "type": "grand_total",
                "label": "Grand Total",
                "opening": total_op,
                "total_debit": total_d,
                "total_credit": total_c,
                "closing": total_cl,
            }
        )
        return lines
