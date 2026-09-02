# -*- coding: utf-8 -*-
"""Daftar Faktur Pajak Pengganti.

Lists customer faktur that were replaced (kode status 01..09 per the Faktur
Pengganti workflow in ``custom_tax_id``), linking the replacement to the
original NSFP — the koreksi trail the tax team reports for the masa.

All Coretax/pengganti fields are read defensively; without the source modules
the report emits an informational note.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportFakturPengganti(models.AbstractModel):
    _name = "custom.report.faktur.pengganti"
    _inherit = "custom.report.engine"
    _description = "Daftar Faktur Pajak Pengganti"

    _report_code = "faktur_pengganti"
    _report_title = "Daftar Faktur Pajak Pengganti"

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Kode", "field": "kode", "kind": "text", "width": 8},
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 28},
            {"header": "NSFP Asal", "field": "nsfp_asal", "kind": "text", "width": 24},
            {"header": "NSFP Pengganti", "field": "nsfp_pengganti", "kind": "text", "width": 24},
            {"header": "DPP", "field": "dpp", "kind": "number", "width": 18},
            {"header": "PPN", "field": "ppn", "kind": "number", "width": 16},
        ]

    def _kode(self, move):
        """Replacement code (custom_tax_id char, else custom_coretax selection)."""
        return self._opt(move, "x_custom_coretax_kode_status") or self._opt(move, "x_custom_coretax_status_code")

    def _is_pengganti(self, move):
        kode = self._kode(move)
        if kode and kode not in ("00", "0"):
            return True
        return bool(self._opt(move, "x_custom_coretax_replacement_of_id", False))

    def _build_lines(self, filters):
        Move = self.env["account.move"]
        has_fields = any(
            f in Move._fields
            for f in (
                "x_custom_coretax_kode_status",
                "x_custom_coretax_status_code",
                "x_custom_coretax_replacement_of_id",
            )
        )
        if not has_fields:
            return [
                {"type": "note", "doc_no": "Modul Faktur Pengganti (custom_tax_id/custom_coretax) belum terpasang."},
                {"type": "grand_total", "doc_no": "TOTAL", "dpp": 0.0, "ppn": 0.0},
            ]

        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
        ]
        if filters.get("posted_only", True):
            domain.append(("state", "=", "posted"))
        moves = Move.search(domain, order="invoice_date, name")

        ppn_ids = self.env["custom.report.faktur.pajak"]._ppn_tax_ids("sale", filters["company_ids"])
        rows = []
        g_dpp = g_ppn = 0.0
        for move in moves.sorted(lambda m: (m.invoice_date or m.date or date_cls.min, m.name or "")):
            if not self._is_pengganti(move):
                continue
            partner = move.commercial_partner_id or move.partner_id
            original = self._opt(move, "x_custom_coretax_replacement_of_id", False)
            dpp, ppn = self._move_dpp_ppn(move, ppn_ids)
            rows.append(
                {
                    "date": move.invoice_date or move.date,
                    "doc_no": move.name or "",
                    "kode": self._kode(move),
                    "partner": partner.display_name or "",
                    "nsfp_asal": self._opt(original, "x_custom_nsfp") if original else "",
                    "nsfp_pengganti": self._opt(move, "x_custom_nsfp"),
                    "dpp": dpp,
                    "ppn": ppn,
                }
            )
            g_dpp += dpp
            g_ppn += ppn

        rows.append({"type": "grand_total", "doc_no": "TOTAL", "dpp": g_dpp, "ppn": g_ppn})
        return rows

    def _move_dpp_ppn(self, move, ppn_ids):
        """(DPP, PPN) of the VAT portion on a customer move, positive."""
        dpp = ppn = 0.0
        if not ppn_ids:
            return dpp, ppn
        for line in move.line_ids:
            if line.tax_line_id and line.tax_line_id.id in ppn_ids:
                ppn += -line.balance
            elif line.tax_ids and any(t.id in ppn_ids for t in line.tax_ids):
                dpp += -line.balance
        return dpp, ppn
