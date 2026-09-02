# -*- coding: utf-8 -*-
"""Petty Cash Aging — outstanding advances bucketed by age.

Reuses the bucket definition from ``custom.report.aged.receivable`` and the
shared report engine (PDF / XLSX / on-screen table). Each open request's
outstanding amount lands in the bucket for its age since disbursement,
aggregated per employee.
"""

from __future__ import annotations

from odoo import models

from odoo.addons.custom_accounting_reports.models.custom_report_aged_receivable import BUCKETS


class PettyCashReportAging(models.AbstractModel):
    _name = "petty.cash.report.aging"
    _inherit = "custom.report.engine"
    _description = "Petty Cash Aging"

    _report_code = "petty_cash_aging"
    _report_title = "Petty Cash Aging"

    def _xlsx_columns(self):
        cols = [{"header": "Employee", "field": "employee_name", "kind": "text", "width": 30}]
        for code, label, _lower, _upper in BUCKETS:
            cols.append({"header": label, "field": code, "kind": "number", "width": 13})
        cols.append({"header": "Total", "field": "total", "kind": "number", "width": 16})
        return cols

    def _classify_bucket(self, age_days):
        if age_days is None or age_days < 0:
            return "not_due"
        for code, _label, lower, upper in BUCKETS:
            if lower is None:
                # "not_due" bucket — only for age 0/negative, handled above.
                continue
            if upper is None:
                if age_days >= lower:
                    return code
            elif lower <= age_days <= upper:
                return code
        return "d_over_365"

    def _open_requests(self, filters):
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("state", "in", ("disbursed", "in_realization")),
        ]
        if filters.get("employee_ids"):
            domain.append(("employee_id", "in", filters["employee_ids"]))
        return self.env["petty.cash.request"].search(domain, order="employee_id, id")

    def _build_lines(self, filters):
        as_of = filters["date_to"]
        bucket_codes = [c for c, _, _, _ in BUCKETS]
        by_emp = {}
        for req in self._open_requests(filters):
            outstanding = req.amount_outstanding
            if req.currency_id.is_zero(outstanding):
                continue
            emp = req.employee_id
            disb_date = req.disburse_move_id.date or req.request_date
            age = (as_of - disb_date).days if disb_date else 0
            bucket = self._classify_bucket(age)
            row = by_emp.setdefault(
                emp.id,
                {
                    "type": "data",
                    "employee_name": emp.name or "—",
                    "total": 0.0,
                    **{c: 0.0 for c in bucket_codes},
                },
            )
            row[bucket] += outstanding
            row["total"] += outstanding

        rows = sorted(by_emp.values(), key=lambda r: (r["employee_name"] or "").lower())
        grand = {"type": "grand_total", "employee_name": "Grand Total", "total": 0.0}
        for c in bucket_codes:
            grand[c] = 0.0
        for row in rows:
            for c in bucket_codes:
                grand[c] += row[c]
            grand["total"] += row["total"]

        return rows + [grand]
