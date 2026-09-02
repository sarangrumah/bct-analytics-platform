# -*- coding: utf-8 -*-
"""Laporan DPP Nilai Lain (PMK 131/2024).

Audit trail of every posted move whose VAT uses ``DPP Nilai Lain`` — the
reduced base introduced by PMK 131/2024 (incl. the PPN 11%-effective-via-12%
transition). Shows the full subtotal, the DPP factor, the resulting reduced
DPP and the PPN, per kategori, so the tax team can defend the computed base.

Reads ``account.tax.x_custom_dpp_*`` from the optional ``custom_tax_id``
module; without it the report emits an informational note.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportDppNilaiLain(models.AbstractModel):
    _name = "custom.report.dpp.nilai.lain"
    _inherit = "custom.report.engine"
    _description = "Laporan DPP Nilai Lain (PMK 131/2024)"

    _report_code = "dpp_nilai_lain"
    _report_title = "Laporan DPP Nilai Lain (PMK 131/2024)"

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 28},
            {"header": "Kategori DPP", "field": "kategori", "kind": "text", "width": 26},
            {"header": "DPP Penuh", "field": "dpp_penuh", "kind": "number", "width": 18},
            {"header": "Faktor", "field": "faktor", "kind": "number", "width": 10},
            {"header": "DPP Nilai Lain", "field": "dpp_nilai_lain", "kind": "number", "width": 18},
            {"header": "PPN", "field": "ppn", "kind": "number", "width": 16},
        ]

    def _build_lines(self, filters):
        Tax = self.env["account.tax"]
        if "x_custom_dpp_method" not in Tax._fields:
            return [
                {"type": "note", "doc_no": "Modul DPP Nilai Lain (custom_tax_id) belum terpasang — tidak ada data."},
                {"type": "grand_total", "doc_no": "TOTAL", "dpp_penuh": 0.0, "dpp_nilai_lain": 0.0, "ppn": 0.0},
            ]

        taxes = Tax.search(
            [
                ("x_custom_dpp_method", "=", "nilai_lain"),
                ("company_id", "in", list(filters["company_ids"])),
            ]
        )
        category_labels = (
            dict(Tax._fields["x_custom_dpp_category"].selection) if "x_custom_dpp_category" in Tax._fields else {}
        )

        AML = self.env["account.move.line"]
        base_domain = self._base_move_line_domain(filters)
        rows = []
        g_penuh = g_nilai = g_ppn = 0.0

        for tax in taxes:
            sign = -1.0 if tax.type_tax_use == "sale" else 1.0
            factor = tax.x_custom_dpp_factor or 1.0
            kategori = category_labels.get(tax.x_custom_dpp_category, tax.x_custom_dpp_category or tax.name)

            base_rows = AML._read_group(
                domain=base_domain + [("tax_ids", "in", [tax.id])],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            base_by_move = {m.id: sign * (b or 0.0) for m, b in base_rows}
            tax_rows = AML._read_group(
                domain=base_domain + [("tax_line_id", "=", tax.id)],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            tax_by_move = {m.id: sign * (b or 0.0) for m, b in tax_rows}

            move_ids = sorted(set(base_by_move) | set(tax_by_move))
            moves = self.env["account.move"].browse(move_ids)
            for move in moves.sorted(lambda m: (m.invoice_date or m.date or date_cls.min, m.name or "")):
                partner = move.commercial_partner_id or move.partner_id
                dpp_penuh = base_by_move.get(move.id, 0.0)
                dpp_nilai = dpp_penuh * factor
                ppn = tax_by_move.get(move.id, 0.0)
                rows.append(
                    {
                        "date": move.invoice_date or move.date,
                        "doc_no": move.name or "",
                        "partner": partner.display_name or "",
                        "kategori": kategori,
                        "dpp_penuh": dpp_penuh,
                        "faktor": factor,
                        "dpp_nilai_lain": dpp_nilai,
                        "ppn": ppn,
                    }
                )
                g_penuh += dpp_penuh
                g_nilai += dpp_nilai
                g_ppn += ppn

        rows.append(
            {
                "type": "grand_total",
                "doc_no": "TOTAL",
                "dpp_penuh": g_penuh,
                "dpp_nilai_lain": g_nilai,
                "ppn": g_ppn,
            }
        )
        return rows
