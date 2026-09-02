# -*- coding: utf-8 -*-
"""Data Quality Lawan Transaksi (NPWP/NIK).

Scans the partners that appear on invoices/bills in the period and flags those
whose NPWP/NIK is missing or invalid — the leading cause of DJP e-Faktur /
e-Bupot rejection. Run it before each Coretax export.

Reads ``res.partner.x_custom_npwp_status`` (from ``custom_tax_id``); without
that module the report emits an informational note.
"""

from __future__ import annotations

from odoo import models


_STATUS_LABEL = {
    "valid": "Valid",
    "invalid": "TIDAK VALID",
    "none": "KOSONG",
}


class CustomReportNpwpQuality(models.AbstractModel):
    _name = "custom.report.npwp.quality"
    _inherit = "custom.report.engine"
    _description = "Data Quality Lawan Transaksi (NPWP/NIK)"

    _report_code = "npwp_quality"
    _report_title = "Data Quality Lawan Transaksi (NPWP/NIK)"

    def _xlsx_columns(self):
        return [
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 32},
            {"header": "NPWP", "field": "npwp", "kind": "text", "width": 22},
            {"header": "NIK", "field": "nik", "kind": "text", "width": 20},
            {"header": "Status NPWP", "field": "status", "kind": "text", "width": 14},
            {"header": "PKP", "field": "pkp", "kind": "text", "width": 8},
            {"header": "Jml Transaksi", "field": "tx_count", "kind": "number", "width": 12},
            {"header": "Masalah", "field": "masalah", "kind": "text", "width": 30},
        ]

    def _build_lines(self, filters):
        Partner = self.env["res.partner"]
        if "x_custom_npwp_status" not in Partner._fields:
            return [
                {"type": "note", "partner": "Modul PPh (custom_tax_id) belum terpasang — tidak ada data."},
                {"type": "grand_total", "partner": "TOTAL", "tx_count": 0},
            ]

        # Partners appearing on moves in scope, with a per-partner move count.
        AML = self.env["account.move.line"]
        domain = self._base_move_line_domain(filters) + [
            ("partner_id", "!=", False),
            ("move_id.move_type", "in", ("out_invoice", "out_refund", "in_invoice", "in_refund")),
        ]
        rows = AML._read_group(
            domain=domain,
            groupby=["partner_id"],
            aggregates=["move_id:count_distinct"],
        )

        lines = []
        problem_count = 0
        for partner, tx_count in rows:
            commercial = partner.commercial_partner_id or partner
            status = self._opt(commercial, "x_custom_npwp_status", "none")
            if status == "valid":
                continue  # only surface data-quality problems
            problem_count += 1
            npwp = self._opt(commercial, "x_custom_npwp")
            nik = self._opt(commercial, "x_custom_nik")
            pkp = self._opt(commercial, "x_custom_pkp", False)
            masalah = "NPWP kosong" if status == "none" else "NPWP tidak valid (bukan 15/16 digit)"
            lines.append(
                {
                    "partner": commercial.display_name or "",
                    "npwp": npwp,
                    "nik": nik,
                    "status": _STATUS_LABEL.get(status, status),
                    "pkp": "PKP" if pkp else "Non-PKP",
                    "tx_count": tx_count or 0,
                    "masalah": masalah,
                }
            )

        lines.sort(key=lambda r: (-(r["tx_count"] or 0), r["partner"]))
        lines.append(
            {
                "type": "grand_total",
                "partner": "TOTAL lawan transaksi bermasalah: %d" % problem_count,
                "tx_count": problem_count,
            }
        )
        return lines
