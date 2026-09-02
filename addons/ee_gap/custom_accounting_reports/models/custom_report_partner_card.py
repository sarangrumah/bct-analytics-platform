# -*- coding: utf-8 -*-
"""Kartu Utang / Kartu Piutang (AP / AR partner cards).

These are richer, single-side variants of the generic Partner Ledger
requested by the Tenant project: a per-partner ledger restricted to the
payable (Kartu Utang) or receivable (Kartu Piutang) control accounts,
with extra document columns (doc currency, clearing document, net due
date, posting user) the generic Partner Ledger intentionally keeps lean.

The generic ``custom.report.partner.ledger`` is left untouched.
"""

from datetime import date as date_cls, timedelta

from odoo import _, models


class CustomReportPartnerCardBase(models.AbstractModel):
    _name = "custom.report.partner.card.base"
    _inherit = "custom.report.engine"
    _description = "Custom Partner Card (Base)"

    # Concrete subclasses set this to the account types to include.
    _card_account_types = ()

    # ------------------------------------------------------------------
    # XLSX columns
    # ------------------------------------------------------------------
    def _xlsx_columns(self):
        return [
            {"header": "Posting Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Document No", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Reference", "field": "reference", "kind": "text", "width": 18},
            {"header": "Account", "field": "account_code", "kind": "text", "width": 12},
            {"header": "Text", "field": "label", "kind": "text", "width": 32},
            {"header": "Debit", "field": "debit", "kind": "number", "width": 16},
            {"header": "Credit", "field": "credit", "kind": "number", "width": 16},
            {"header": "Balance", "field": "running_balance", "kind": "number", "width": 16},
            {"header": "Amount (Doc Curr.)", "field": "amount_currency", "kind": "number", "width": 16},
            {"header": "Doc Curr.", "field": "currency", "kind": "text", "width": 9},
            {"header": "Clearing Doc", "field": "clearing", "kind": "text", "width": 16},
            {"header": "Net Due Date", "field": "due_date", "kind": "date", "width": 12},
            {"header": "User", "field": "user", "kind": "text", "width": 18},
        ]

    # ------------------------------------------------------------------
    # XLSX body (per-partner grouped)
    # ------------------------------------------------------------------
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
                elif field == "running_balance":
                    sheet.write_number(row, col_idx, float(line.get("closing") or 0.0), fmt_num)
                elif col["kind"] == "number":
                    sheet.write_number(row, col_idx, 0.0, fmt_num)
                else:
                    sheet.write(row, col_idx, "", fmt_text)

        for line in ctx.get("lines", []):
            ltype = line.get("type")
            if ltype == "partner":
                heading = "%s  —  Opening: %s" % (
                    line.get("partner_name") or "",
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
                sheet.merge_range(row, 0, row, debit_i - 1, "Total", fmts["total_text"])
                _write_totals(line, fmts["total_num"], fmts["total_text"])
                row += 1
            elif ltype == "grand_total":
                sheet.merge_range(row, 0, row, debit_i - 1, line.get("label") or "Grand Total", fmts["total_text"])
                _write_totals(line, fmts["total_num"], fmts["total_text"])
                row += 1
        return row

    # ------------------------------------------------------------------
    # On-screen flattening
    # ------------------------------------------------------------------
    def _flatten_for_screen(self, lines, columns):
        """Expand the per-partner blocks into display rows.

        ``_build_lines`` returns one ``partner`` row per counterparty holding
        its movements in a nested ``lines`` list — a shape the engine's default
        flattener knows nothing about, so it emitted one blank row per partner
        and the card looked empty on screen even though every figure was
        present (sheet item EO #3, "kartu utang dan piutang belum terintegrasi
        dengan data yang telah dilakukan input"). The XLSX export has its own
        body writer and was never affected, which is why the gap only showed in
        the UI.
        """
        first_text, second_text = self._text_fields(columns)
        # Name the balance column explicitly: the last numeric column is
        # "Amount (Doc Curr.)", so picking it positionally would file the
        # opening/closing balance under the document-currency column.
        fields_present = {c["field"] for c in columns}
        balance_field = "running_balance" if "running_balance" in fields_present else None
        rows = []

        for line in lines:
            if line.get("type") != "partner":
                rows.append(self._screen_row(line, columns, first_text))
                continue

            header = {first_text: line.get("partner_name") or "—"}
            if second_text and second_text != first_text:
                header[second_text] = _("Saldo awal: %s", self._format_amount(line.get("opening") or 0.0))
            if balance_field:
                header[balance_field] = line.get("opening") or 0.0
            row = self._screen_row(header, columns, first_text)
            row["type"] = "header"
            row["level"] = 0
            rows.append(row)

            for movement in line.get("lines") or []:
                child = self._screen_row(movement, columns, first_text)
                child["level"] = 1
                rows.append(child)

            total = {
                first_text: _("Total %s", line.get("partner_name") or "—"),
                "debit": line.get("total_debit") or 0.0,
                "credit": line.get("total_credit") or 0.0,
            }
            if balance_field:
                total[balance_field] = line.get("closing") or 0.0
            trow = self._screen_row(total, columns, first_text)
            trow["type"] = "total"
            trow["level"] = 0
            rows.append(trow)

        return rows

    # ------------------------------------------------------------------
    # Line builder
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        types = list(self._card_account_types)
        AML = self.env["account.move.line"]

        opening_filters = dict(
            filters,
            date_from=date_cls(1970, 1, 1),
            date_to=filters["date_from"] - timedelta(days=1),
        )
        opening_rows = AML._read_group(
            domain=self._base_move_line_domain(opening_filters) + [("account_id.account_type", "in", types)],
            groupby=["partner_id"],
            aggregates=["debit:sum", "credit:sum"],
        )
        opening_by_partner = {(p.id if p else 0): (d or 0.0) - (c or 0.0) for p, d, c in opening_rows}

        period_domain = self._base_move_line_domain(filters) + [
            ("account_id.account_type", "in", types),
        ]
        if filters.get("partner_ids"):
            period_domain.append(("partner_id", "in", filters["partner_ids"]))
        period_lines = AML.search(period_domain, order="partner_id, date, id")

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
                    "doc_no": ml.move_id.name or "",
                    "reference": ml.move_id.ref or ml.ref or "",
                    "account_code": self._account_code(ml.account_id),
                    "label": ml.name or "",
                    "debit": ml.debit,
                    "credit": ml.credit,
                    "running_balance": running,
                    "amount_currency": ml.amount_currency or 0.0,
                    "currency": ml.currency_id.name or "",
                    "clearing": ml.matching_number or "",
                    "due_date": ml.date_maturity,
                    "user": ml.create_uid.name or "",
                }
            )

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
        total_d = total_c = total_cl = 0.0
        for entry in sorted(partners.values(), key=lambda r: r["partner_name"]):
            closing = entry["opening"] + entry["total_debit"] - entry["total_credit"]
            entry["closing"] = closing
            lines.append(entry)
            total_d += entry["total_debit"]
            total_c += entry["total_credit"]
            total_cl += closing

        lines.append(
            {
                "type": "grand_total",
                "label": "Grand Total",
                "total_debit": total_d,
                "total_credit": total_c,
                "closing": total_cl,
            }
        )
        return lines


class CustomReportPayableCard(models.AbstractModel):
    _name = "custom.report.payable.card"
    _inherit = "custom.report.partner.card.base"
    _description = "Kartu Utang (AP Card)"

    _report_code = "payable_card"
    _report_title = "Kartu Utang"
    _card_account_types = ("liability_payable",)


class CustomReportReceivableCard(models.AbstractModel):
    _name = "custom.report.receivable.card"
    _inherit = "custom.report.partner.card.base"
    _description = "Kartu Piutang (AR Card)"

    _report_code = "receivable_card"
    _report_title = "Kartu Piutang"
    _card_account_types = ("asset_receivable",)
