# -*- coding: utf-8 -*-
"""Jejak Audit Pajak — PDP Audit Log (TAX-MON-04).

Extracts the tamper-evident PDP audit trail (``pdp.audit.log``, hash-chained per
UU 27/2022) for tax-relevant activity: postings on tax documents, withholding
application, bukti-potong and Coretax submissions, and rule/category changes.
Gives the tax team an audit-defense export of *who did what, when* on the tax
records.

The audit log is DB-wide (one tenant per DB); there is no company filter.
"""

from __future__ import annotations

from datetime import datetime, time

from odoo import models


class CustomReportTaxAudit(models.AbstractModel):
    _name = "custom.report.tax.audit"
    _inherit = "custom.report.engine"
    _description = "Jejak Audit Pajak (PDP Audit Log)"

    _report_code = "tax_audit"
    _report_title = "Jejak Audit Pajak (PDP Audit Log)"

    _LIMIT = 5000

    # Tax-relevant models tracked in the PDP audit log (shared with the wizard
    # drill-down).
    TAX_MODELS = [
        "account.move",
        "account.move.line",
        "account.move.withholding.line",
        "custom.coretax.bukti.potong",
        "custom.coretax.transaction",
        "custom.bupot.unifikasi",
        "custom.bupot.unifikasi.line",
        "tax.withholding.rule",
        "tax.withholding.category",
    ]

    def _xlsx_columns(self):
        return [
            {"header": "Waktu (UTC)", "field": "ts", "kind": "text", "width": 20},
            {"header": "User", "field": "user", "kind": "text", "width": 22},
            {"header": "Model", "field": "model", "kind": "text", "width": 30},
            {"header": "Res ID", "field": "res_id", "kind": "number", "width": 10},
            {"header": "Aksi", "field": "action", "kind": "text", "width": 22},
            {"header": "Klasifikasi", "field": "classification", "kind": "text", "width": 14},
            {"header": "Alasan / Catatan", "field": "reason", "kind": "text", "width": 34},
        ]

    def _build_lines(self, filters):
        if "pdp.audit.log" not in self.env:
            return [
                {"type": "note", "user": "Modul PDP Audit (custom_pdp_audit) belum terpasang."},
                {"type": "grand_total", "user": "TOTAL", "res_id": 0},
            ]

        Log = self.env["pdp.audit.log"].sudo()
        dt_from = datetime.combine(filters["date_from"], time.min)
        dt_to = datetime.combine(filters["date_to"], time.max)
        domain = [
            ("ts", ">=", dt_from),
            ("ts", "<=", dt_to),
            "|",
            ("model_name", "in", self.TAX_MODELS),
            ("action", "=", "pph_withholding_applied"),
        ]
        records = Log.search(domain, order="ts desc", limit=self._LIMIT)

        rows = []
        for r in records:
            rows.append(
                {
                    "ts": r.ts.strftime("%d/%m/%Y %H:%M:%S") if r.ts else "",
                    "user": r.actor_login or (str(r.actor_user_id) if r.actor_user_id else "system"),
                    "model": r.model_name or "",
                    "res_id": r.res_id or 0,
                    "action": r.action or "",
                    "classification": r.classification or "",
                    "reason": (r.reason or "")[:200],
                }
            )

        capped = len(records) >= self._LIMIT
        rows.append(
            {
                "type": "grand_total",
                "user": "TOTAL entri: %d%s" % (len(records), " (dibatasi, persempit masa)" if capped else ""),
                "res_id": len(records),
            }
        )
        return rows
