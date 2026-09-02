# -*- coding: utf-8 -*-
"""Aged Receivable: open AR bucketed by overdue days.

Two layouts, switched by the context key ``aging_detail``:

* ``summary`` — one row per partner with the open balance spread across
  overdue buckets. Always used by the PDF.
* ``detail`` (default) — one row per open document, grouped by partner with
  a partner subtotal, carrying the document number, the counterparty's own
  reference and the receivable/payable account. Honoured by the on-screen
  table and the Excel export.
"""

from odoo import models


BUCKETS = (
    ("not_due", "Not Due", None, 0),
    ("d_0_30", "0-30", 0, 30),
    ("d_31_60", "31-60", 31, 60),
    ("d_61_90", "61-90", 61, 90),
    ("d_91_180", "91-180", 91, 180),
    ("d_181_365", "181-365", 181, 365),
    ("d_over_365", "365+", 366, None),
)


class CustomReportAgedReceivable(models.AbstractModel):
    _name = "custom.report.aged.receivable"
    _inherit = "custom.report.engine"
    _description = "Custom Aged Receivable"

    _report_code = "aged_receivable"
    _report_title = "Aged Receivable"

    # ------------------------------------------------------------------
    # Layout switch
    # ------------------------------------------------------------------
    def _is_detail(self):
        return bool(self.env.context.get("aging_detail"))

    # ------------------------------------------------------------------
    # XLSX columns
    # ------------------------------------------------------------------
    def _xlsx_columns(self):
        if self._is_detail():
            return self._detail_columns()
        cols = [{"header": "Partner", "field": "partner_name", "kind": "text", "width": 36}]
        for code, label, _lower, _upper in BUCKETS:
            cols.append({"header": label, "field": code, "kind": "number", "width": 13})
        cols.append({"header": "Total", "field": "total", "kind": "number", "width": 16})
        return cols

    # Headers Finance asked for; ``custom.report.aged.payable`` overrides them
    # with the vendor-bill wording.
    _doc_no_header = "Invoice Number"
    _reference_header = "Invoice Reference"
    _account_header = "Receivable Account"

    def _detail_columns(self):
        cols = [
            {"header": "Partner", "field": "partner_name", "kind": "text", "width": 30},
            {"header": self._doc_no_header, "field": "doc_no", "kind": "text", "width": 18},
            {"header": self._reference_header, "field": "reference", "kind": "text", "width": 18},
            {"header": "Doc Date", "field": "doc_date", "kind": "date", "width": 12},
            {"header": "Due Date", "field": "due_date", "kind": "date", "width": 12},
            {"header": "Overdue Days", "field": "overdue_days", "kind": "text", "width": 11},
            {"header": self._account_header, "field": "account_code", "kind": "text", "width": 18},
        ]
        for code, label, _lower, _upper in BUCKETS:
            cols.append({"header": label, "field": code, "kind": "number", "width": 13})
        cols.append({"header": "Total", "field": "total", "kind": "number", "width": 16})
        return cols

    # ------------------------------------------------------------------
    # On-screen table
    # ------------------------------------------------------------------
    def _flatten_for_screen(self, lines, columns):
        """``_build_lines`` returns an aging *dict*, not the list of row dicts
        the engine's default flattener expects."""
        data = lines or {}
        grand = dict(data.get("grand_total", {}), partner_name="Grand Total")
        out = []
        if self._is_detail():
            for group in data.get("partners", []):
                for row in group.get("rows", []):
                    out.append(self._screen_row(row, columns))
                subtotal = dict(
                    group.get("subtotal", {}),
                    partner_name="Subtotal %s" % (group.get("partner_name") or ""),
                    type="subtotal",
                )
                out.append(self._screen_row(subtotal, columns))
        else:
            for row in data.get("rows", []):
                out.append(self._screen_row(row, columns))
        grand["type"] = "grand_total"
        out.append(self._screen_row(grand, columns))
        return out

    # ------------------------------------------------------------------
    # XLSX body
    # ------------------------------------------------------------------
    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        if self._is_detail():
            return self._xlsx_detail_body(sheet, ctx, columns, fmts, start_row)

        data = ctx.get("lines") or {}
        rows = data.get("rows", [])
        buckets = data.get("buckets", [])
        grand = data.get("grand_total", {})
        ncol = len(buckets) + 2  # partner + buckets + total
        row = start_row

        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col["header"], fmts["header"])
        sheet.freeze_panes(row + 1, 1)
        row += 1

        for r in rows:
            sheet.write(row, 0, r.get("partner_name") or "", fmts["text"])
            for i, b in enumerate(buckets):
                sheet.write_number(row, 1 + i, float(r.get(b["code"]) or 0.0), fmts["num"])
            sheet.write_number(row, ncol - 1, float(r.get("total") or 0.0), fmts["num"])
            row += 1

        sheet.write(row, 0, "Grand Total", fmts["total_text"])
        for i, b in enumerate(buckets):
            sheet.write_number(row, 1 + i, float(grand.get(b["code"]) or 0.0), fmts["total_num"])
        sheet.write_number(row, ncol - 1, float(grand.get("total") or 0.0), fmts["total_num"])
        return row + 1

    def _xlsx_detail_body(self, sheet, ctx, columns, fmts, start_row):
        data = ctx.get("lines") or {}
        buckets = data.get("buckets", [])
        groups = data.get("partners", [])
        grand = data.get("grand_total", {})
        ncol = len(columns)
        bucket_start = ncol - len(buckets) - 1  # first bucket column index
        row = start_row

        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col["header"], fmts["header"])
        sheet.freeze_panes(row + 1, 1)
        row += 1

        def _write_amounts(r, source, fmt_num):
            for i, b in enumerate(buckets):
                sheet.write_number(row, bucket_start + i, float(source.get(b["code"]) or 0.0), fmt_num)
            sheet.write_number(row, ncol - 1, float(source.get("total") or 0.0), fmt_num)

        for grp in groups:
            for r in grp.get("rows", []):
                for col_idx, col in enumerate(columns[:bucket_start]):
                    value = r.get(col["field"])
                    if col.get("kind") == "date":
                        value = self._format_date_id(value)
                    sheet.write(row, col_idx, value or "", fmts["text"])
                _write_amounts(r, r, fmts["num"])
                row += 1
            # Partner subtotal
            sheet.merge_range(
                row,
                0,
                row,
                bucket_start - 1,
                "Subtotal %s" % (grp.get("partner_name") or ""),
                fmts["total_text"],
            )
            _write_amounts(grp, grp.get("subtotal", {}), fmts["total_num"])
            row += 1

        sheet.merge_range(row, 0, row, bucket_start - 1, "Grand Total", fmts["total_text"])
        _write_amounts(grand, grand, fmts["total_num"])
        return row + 1

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def _account_type(self):
        return "asset_receivable"

    def _classify_bucket(self, due_date, as_of):
        if not due_date or due_date >= as_of:
            return "not_due"
        days = (as_of - due_date).days
        if days <= 0:
            return "not_due"
        for code, _label, lower, upper in BUCKETS:
            if lower is None:
                continue
            if upper is None:
                if days >= lower:
                    return code
            elif lower <= days <= upper:
                return code
        return "d_over_365"

    def _open_lines(self, filters):
        """Open (unreconciled) move lines for this report's account type."""
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("account_id.account_type", "=", self._account_type()),
            ("parent_state", "=", "posted"),
            ("reconciled", "=", False),
            ("date", "<=", filters["date_to"]),
        ]
        if filters.get("partner_ids"):
            domain.append(("partner_id", "in", filters["partner_ids"]))
        return self.env["account.move.line"].search(domain, order="partner_id, date_maturity, id")

    # ------------------------------------------------------------------
    # Line builders
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        if self._is_detail():
            return self._build_detail_lines(filters)
        return self._build_summary_lines(filters)

    def _build_summary_lines(self, filters):
        as_of = filters["date_to"]
        per_partner = {}
        for line in self._open_lines(filters):
            residual = line.amount_residual
            if not residual:
                continue
            pid = line.partner_id.id or 0
            row = per_partner.setdefault(
                pid,
                {
                    "partner_id": pid,
                    "partner_name": (line.partner_id.display_name or "No Partner"),
                    "total": 0.0,
                    **{code: 0.0 for code, _, _, _ in BUCKETS},
                },
            )
            bucket = self._classify_bucket(line.date_maturity or line.date, as_of)
            row[bucket] += residual
            row["total"] += residual

        partners = sorted(
            per_partner.values(),
            key=lambda r: (r["partner_name"] or "").lower(),
        )
        grand_total = {c: 0.0 for c, _, _, _ in BUCKETS}
        grand_total["total"] = 0.0
        for row in partners:
            for code, _, _, _ in BUCKETS:
                grand_total[code] += row[code]
            grand_total["total"] += row["total"]

        return {
            "type": "aging",
            "buckets": [{"code": c, "label": label} for c, label, _, _ in BUCKETS],
            "rows": partners,
            "grand_total": grand_total,
        }

    def _build_detail_lines(self, filters):
        as_of = filters["date_to"]
        bucket_codes = [c for c, _, _, _ in BUCKETS]
        groups = {}
        for line in self._open_lines(filters):
            residual = line.amount_residual
            if not residual:
                continue
            pid = line.partner_id.id or 0
            due = line.date_maturity or line.date
            bucket = self._classify_bucket(due, as_of)
            overdue = max((as_of - due).days, 0) if due else 0
            detail = {
                "partner_name": line.partner_id.display_name or "No Partner",
                "doc_no": line.move_id.name or "",
                "reference": line.move_id.ref or line.ref or "",
                "doc_date": line.date,
                "due_date": line.date_maturity,
                "overdue_days": str(overdue),
                "account_code": self._account_code(line.account_id),
                "total": residual,
                **{c: 0.0 for c in bucket_codes},
            }
            detail[bucket] = residual
            grp = groups.setdefault(
                pid,
                {
                    "partner_name": detail["partner_name"],
                    "rows": [],
                    "subtotal": {**{c: 0.0 for c in bucket_codes}, "total": 0.0},
                },
            )
            grp["rows"].append(detail)
            for c in bucket_codes:
                grp["subtotal"][c] += detail[c]
            grp["subtotal"]["total"] += residual

        ordered = sorted(groups.values(), key=lambda g: (g["partner_name"] or "").lower())
        grand_total = {c: 0.0 for c in bucket_codes}
        grand_total["total"] = 0.0
        for grp in ordered:
            for c in bucket_codes:
                grand_total[c] += grp["subtotal"][c]
            grand_total["total"] += grp["subtotal"]["total"]

        return {
            "type": "aging_detail",
            "buckets": [{"code": c, "label": label} for c, label, _, _ in BUCKETS],
            "partners": ordered,
            "grand_total": grand_total,
        }
