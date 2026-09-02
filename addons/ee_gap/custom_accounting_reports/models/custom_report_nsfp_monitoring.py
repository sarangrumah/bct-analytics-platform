# -*- coding: utf-8 -*-
"""Monitoring NSFP & Status Faktur (Coretax).

Lists posted customer invoices/refunds in the period and their Coretax
lifecycle state — helping the tax team catch faktur that are still without an
NSFP (No. Faktur Pajak) as the reporting deadline approaches, or that were
rejected by DJP.

All Coretax fields are read defensively (from the optional ``custom_coretax``
module); without it the report emits an informational note.
"""

from __future__ import annotations

from odoo import fields, models


_STATUS_LABEL = {
    "draft": "Belum submit",
    "submitted": "Submitted",
    "approved": "Approved DJP",
    "rejected_djp": "Rejected DJP",
}


class CustomReportNsfpMonitoring(models.AbstractModel):
    _name = "custom.report.nsfp.monitoring"
    _inherit = "custom.report.engine"
    _description = "Monitoring NSFP & Status Faktur"

    _report_code = "nsfp_monitoring"
    _report_title = "Monitoring NSFP & Status Faktur"

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 30},
            {"header": "PPN", "field": "ppn", "kind": "number", "width": 16},
            {"header": "Status Coretax", "field": "status", "kind": "text", "width": 16},
            {"header": "NSFP", "field": "nsfp", "kind": "text", "width": 24},
            {"header": "Umur (hari)", "field": "umur", "kind": "number", "width": 10},
            {"header": "Keterangan", "field": "keterangan", "kind": "text", "width": 22},
        ]

    def _build_lines(self, filters):
        Move = self.env["account.move"]
        if "x_custom_coretax_status" not in Move._fields and "x_custom_nsfp" not in Move._fields:
            return [
                {"type": "note", "doc_no": "Modul Coretax (custom_coretax) belum terpasang — tidak ada data."},
                {"type": "grand_total", "doc_no": "TOTAL", "ppn": 0.0},
            ]

        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
        ]
        if filters.get("posted_only", True):
            domain.append(("state", "=", "posted"))
        else:
            domain.append(("state", "in", ("draft", "posted")))
        moves = Move.search(domain, order="invoice_date, name")

        today = fields.Date.context_today(self)
        rows = []
        grand_ppn = 0.0
        no_nsfp = 0
        for move in moves:
            partner = move.commercial_partner_id or move.partner_id
            status = self._opt(move, "x_custom_coretax_status")
            nsfp = self._opt(move, "x_custom_nsfp")
            ref_date = move.invoice_date or move.date
            umur = (today - ref_date).days if ref_date else 0
            if nsfp:
                keterangan = "OK"
            elif status == "rejected_djp":
                keterangan = "DITOLAK DJP"
            else:
                keterangan = "BELUM ber-NSFP"
                no_nsfp += 1
            ppn = self._move_ppn(move)
            grand_ppn += ppn
            rows.append(
                {
                    "date": ref_date,
                    "doc_no": move.name or "",
                    "partner": partner.display_name or "",
                    "ppn": ppn,
                    "status": _STATUS_LABEL.get(status, status or ""),
                    "nsfp": nsfp,
                    "umur": umur,
                    "keterangan": keterangan,
                }
            )

        rows.append(
            {
                "type": "grand_total",
                "doc_no": "TOTAL",
                "partner": "%d faktur, %d belum ber-NSFP" % (len(rows), no_nsfp),
                "ppn": grand_ppn,
            }
        )
        return rows

    def _move_ppn(self, move):
        """Sum of the VAT (non-PPh) tax lines on a customer move (positive)."""
        total = 0.0
        for line in move.line_ids:
            if line.tax_line_id and not (line.tax_line_id.name or "").upper().startswith("PPH"):
                total += -line.balance  # sale side: tax line credited
        return total
