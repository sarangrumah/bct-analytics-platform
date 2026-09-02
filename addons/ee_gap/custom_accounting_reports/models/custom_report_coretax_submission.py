# -*- coding: utf-8 -*-
"""Monitoring Status Submission Coretax / Pajakku (ASPP).

Lists every Pajakku submission (``custom.coretax.transaction``) in the period,
grouped by state — so ops/tax can see what is queued, in-flight, approved,
rejected or errored, and chase the DJP round-trip before the deadline.

Reads the optional ``custom_coretax_pajakku`` ledger; without it the report
emits an informational note.
"""

from __future__ import annotations

from datetime import datetime, time

from odoo import models


_STATE_ORDER = ["error", "rejected", "queued", "submitting", "submitted", "approved"]
_STATE_LABEL = {
    "queued": "Queued",
    "submitting": "Submitting",
    "submitted": "Submitted (menunggu DJP)",
    "approved": "Approved",
    "rejected": "Rejected DJP",
    "error": "Error",
}


class CustomReportCoretaxSubmission(models.AbstractModel):
    _name = "custom.report.coretax.submission"
    _inherit = "custom.report.engine"
    _description = "Monitoring Submission Coretax / Pajakku"

    _report_code = "coretax_submission"
    _report_title = "Monitoring Submission Coretax / Pajakku"

    def _xlsx_columns(self):
        return [
            {"header": "Dibuat", "field": "date", "kind": "date", "width": 12},
            {"header": "Transaksi", "field": "name", "kind": "text", "width": 30},
            {"header": "Jenis", "field": "jenis", "kind": "text", "width": 20},
            {"header": "Dokumen", "field": "doc", "kind": "text", "width": 20},
            {"header": "Status", "field": "status", "kind": "text", "width": 20},
            {"header": "NSFP/No.Bupot", "field": "nsfp", "kind": "text", "width": 22},
            {"header": "Retry", "field": "retry", "kind": "number", "width": 8},
            {"header": "Pesan DJP", "field": "pesan", "kind": "text", "width": 30},
        ]

    def _build_lines(self, filters):
        if "custom.coretax.transaction" not in self.env:
            return [
                {"type": "note", "name": "Modul Pajakku (custom_coretax_pajakku) belum terpasang — tidak ada data."},
                {"type": "grand_total", "name": "TOTAL", "retry": 0},
            ]

        Tx = self.env["custom.coretax.transaction"].sudo()
        type_labels = dict(Tx._fields["transaction_type"].selection)
        dt_from = datetime.combine(filters["date_from"], time.min)
        dt_to = datetime.combine(filters["date_to"], time.max)
        records = Tx.search(
            [
                ("company_id", "in", list(filters["company_ids"])),
                ("create_date", ">=", dt_from),
                ("create_date", "<=", dt_to),
            ]
        )

        buckets = {}
        for tx in records:
            doc = (tx.account_move_id.name or (tx.bukti_potong_id.no_bupot if tx.bukti_potong_id else "")) or ""
            buckets.setdefault(tx.state, []).append(
                {
                    "date": tx.create_date.date() if tx.create_date else False,
                    "name": tx.name or "",
                    "jenis": type_labels.get(tx.transaction_type, tx.transaction_type or ""),
                    "doc": doc,
                    "status": _STATE_LABEL.get(tx.state, tx.state or ""),
                    "nsfp": tx.nsfp or "",
                    "retry": tx.retry_count or 0,
                    "pesan": (tx.djp_message or tx.last_error or "")[:120],
                }
            )

        lines = []
        total = 0
        total_retry = 0
        ordered = [s for s in _STATE_ORDER if s in buckets] + [s for s in buckets if s not in _STATE_ORDER]
        for state in ordered:
            group = buckets[state]
            for r in sorted(group, key=lambda r: r["name"]):
                lines.append(r)
            s_retry = sum(r["retry"] for r in group)
            lines.append(
                {
                    "type": "subtotal",
                    "name": "Subtotal %s (%d transaksi)" % (_STATE_LABEL.get(state, state), len(group)),
                    "retry": s_retry,
                }
            )
            total += len(group)
            total_retry += s_retry

        lines.append({"type": "grand_total", "name": "TOTAL: %d transaksi" % total, "retry": total_retry})
        return lines
