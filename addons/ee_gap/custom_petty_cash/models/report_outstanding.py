# -*- coding: utf-8 -*-
"""Petty Cash Outstanding — open advances per employee.

Subclasses the shared ``custom.report.engine`` so it gets the PDF / XLSX /
on-screen table surfaces for free. Rows are built from ``petty.cash.request``
records (one row per open request, an employee subtotal, a grand total),
which keeps the report aligned with the operational documents rather than the
raw advance ledger.
"""

from __future__ import annotations

from odoo import models


class PettyCashReportOutstanding(models.AbstractModel):
    _name = "petty.cash.report.outstanding"
    _inherit = "custom.report.engine"
    _description = "Petty Cash Outstanding"

    _report_code = "petty_cash_outstanding"
    _report_title = "Petty Cash Outstanding"

    def _xlsx_columns(self):
        return [
            {"header": "Employee", "field": "employee_name", "kind": "text", "width": 26},
            {"header": "Reference", "field": "reference", "kind": "text", "width": 18},
            {"header": "Operating Unit", "field": "ou_name", "kind": "text", "width": 22},
            {"header": "Request Date", "field": "request_date", "kind": "date", "width": 12},
            {"header": "Deadline", "field": "deadline", "kind": "date", "width": 12},
            {"header": "Age (days)", "field": "age_days", "kind": "text", "width": 10},
            {"header": "Disbursed", "field": "disbursed", "kind": "number", "width": 15},
            {"header": "Realized", "field": "realized", "kind": "number", "width": 15},
            {"header": "Outstanding", "field": "outstanding", "kind": "number", "width": 15},
            {"header": "Status", "field": "status", "kind": "text", "width": 12},
        ]

    def _open_requests(self, filters):
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("state", "in", ("disbursed", "in_realization")),
        ]
        if filters.get("employee_ids"):
            domain.append(("employee_id", "in", filters["employee_ids"]))
        return self.env["petty.cash.request"].search(domain, order="employee_id, request_date, id")

    def _build_lines(self, filters):
        as_of = filters["date_to"]
        requests = self._open_requests(filters)
        by_emp = {}
        for req in requests:
            if req.currency_id.is_zero(req.amount_outstanding):
                continue
            emp = req.employee_id
            bucket = by_emp.setdefault(
                emp.id,
                {"name": emp.name or "—", "rows": [], "disbursed": 0.0, "realized": 0.0, "outstanding": 0.0},
            )
            disb_date = req.disburse_move_id.date or req.request_date
            age = (as_of - disb_date).days if disb_date else 0
            overdue = bool(req.realization_deadline and req.realization_deadline < as_of)
            bucket["rows"].append(
                {
                    "type": "data",
                    "employee_name": emp.name or "—",
                    "reference": req.name,
                    "ou_name": req.l10n_ou_analytic_id.name or "",
                    "request_date": req.request_date,
                    "deadline": req.realization_deadline,
                    "age_days": str(max(age, 0)),
                    "disbursed": req.amount_disbursed,
                    "realized": req.amount_realized,
                    "outstanding": req.amount_outstanding,
                    "status": "Overdue" if overdue else "Open",
                }
            )
            bucket["disbursed"] += req.amount_disbursed
            bucket["realized"] += req.amount_realized
            bucket["outstanding"] += req.amount_outstanding

        lines = []
        g_disb = g_real = g_out = 0.0
        for emp_id in sorted(by_emp, key=lambda e: (by_emp[e]["name"] or "").lower()):
            b = by_emp[emp_id]
            lines.extend(b["rows"])
            lines.append(
                {
                    "type": "subtotal",
                    "employee_name": "Subtotal %s" % b["name"],
                    "disbursed": b["disbursed"],
                    "realized": b["realized"],
                    "outstanding": b["outstanding"],
                }
            )
            g_disb += b["disbursed"]
            g_real += b["realized"]
            g_out += b["outstanding"]

        lines.append(
            {
                "type": "grand_total",
                "employee_name": "Grand Total",
                "disbursed": g_disb,
                "realized": g_real,
                "outstanding": g_out,
            }
        )
        return lines
