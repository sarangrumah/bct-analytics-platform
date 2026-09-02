# -*- coding: utf-8 -*-
"""Usage / Billing Meter Pajakku (ASPP).

Per-company per-month API usage (``custom.coretax.pajakku.usage``): API calls,
faktur & bupot submissions, errors — for billing reconciliation and quota
control. Reads the optional ``custom_coretax_pajakku`` module; without it the
report emits an informational note.
"""

from __future__ import annotations

from odoo import models


class CustomReportPajakkuUsage(models.AbstractModel):
    _name = "custom.report.pajakku.usage"
    _inherit = "custom.report.engine"
    _description = "Usage Meter Pajakku (ASPP)"

    _report_code = "pajakku_usage"
    _report_title = "Usage Meter Pajakku (ASPP)"

    def _xlsx_columns(self):
        return [
            {"header": "Periode", "field": "period", "kind": "date", "width": 14},
            {"header": "Company", "field": "company", "kind": "text", "width": 30},
            {"header": "API Calls", "field": "api_calls", "kind": "number", "width": 14},
            {"header": "Faktur Submits", "field": "faktur_submits", "kind": "number", "width": 16},
            {"header": "Bupot Submits", "field": "bupot_submits", "kind": "number", "width": 16},
            {"header": "Errors", "field": "errors", "kind": "number", "width": 12},
        ]

    def _build_lines(self, filters):
        if "custom.coretax.pajakku.usage" not in self.env:
            return [
                {"type": "note", "company": "Modul Pajakku (custom_coretax_pajakku) belum terpasang — tidak ada data."},
                {
                    "type": "grand_total",
                    "company": "TOTAL",
                    "api_calls": 0,
                    "faktur_submits": 0,
                    "bupot_submits": 0,
                    "errors": 0,
                },
            ]

        Usage = self.env["custom.coretax.pajakku.usage"].sudo()
        records = Usage.search(
            [
                ("company_id", "in", list(filters["company_ids"])),
                ("period", ">=", filters["date_from"]),
                ("period", "<=", filters["date_to"]),
            ],
            order="period desc, company_id",
        )

        rows = []
        g_api = g_faktur = g_bupot = g_err = 0.0
        for u in records:
            rows.append(
                {
                    "period": u.period,
                    "company": u.company_id.name or "",
                    "api_calls": u.api_calls or 0,
                    "faktur_submits": u.faktur_submits or 0,
                    "bupot_submits": u.bupot_submits or 0,
                    "errors": u.errors or 0,
                }
            )
            g_api += u.api_calls or 0
            g_faktur += u.faktur_submits or 0
            g_bupot += u.bupot_submits or 0
            g_err += u.errors or 0

        rows.append(
            {
                "type": "grand_total",
                "company": "TOTAL",
                "api_calls": g_api,
                "faktur_submits": g_faktur,
                "bupot_submits": g_bupot,
                "errors": g_err,
            }
        )
        return rows
