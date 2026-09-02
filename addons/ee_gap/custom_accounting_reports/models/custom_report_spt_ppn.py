# -*- coding: utf-8 -*-
"""SPT Masa PPN 1111 — Induk (ringkasan masa).

Aggregates the period's output/input VAT into the induk summary the tax team
files: DPP & PPN Keluaran, DPP & PPN Masukan yang dapat dikreditkan, and the
resulting PPN Kurang/(Lebih) Bayar. The per-faktur detail lives in
``custom.report.faktur.pajak`` (the 1111 lampiran); this is the cover page.

VAT tax resolution is delegated to ``custom.report.faktur.pajak._ppn_tax_ids``
so both reports agree on what counts as PPN.
"""

from __future__ import annotations

from odoo import models


class CustomReportSptPpn(models.AbstractModel):
    _name = "custom.report.spt.ppn"
    _inherit = "custom.report.engine"
    _description = "SPT Masa PPN 1111 (Induk)"

    _report_code = "spt_ppn"
    _report_title = "SPT Masa PPN 1111 (Induk)"

    def _xlsx_columns(self):
        return [
            {"header": "Uraian", "field": "uraian", "kind": "text", "width": 52},
            {"header": "DPP", "field": "dpp", "kind": "number", "width": 22},
            {"header": "PPN", "field": "ppn", "kind": "number", "width": 22},
        ]

    def _ppn_totals(self, filters, type_tax_use, sign):
        """Return ``(dpp, ppn)`` for one VAT side over the period."""
        ppn_ids = self.env["custom.report.faktur.pajak"]._ppn_tax_ids(type_tax_use, filters["company_ids"])
        if not ppn_ids:
            return 0.0, 0.0
        AML = self.env["account.move.line"]
        base_domain = self._base_move_line_domain(filters)
        dpp_rows = AML._read_group(
            domain=base_domain + [("tax_ids", "in", ppn_ids)],
            aggregates=["balance:sum"],
        )
        tax_rows = AML._read_group(
            domain=base_domain + [("tax_line_id", "in", ppn_ids)],
            aggregates=["balance:sum"],
        )
        dpp = sign * (dpp_rows[0][0] or 0.0) if dpp_rows else 0.0
        ppn = sign * (tax_rows[0][0] or 0.0) if tax_rows else 0.0
        return dpp, ppn

    def _build_lines(self, filters):
        dpp_out, ppn_out = self._ppn_totals(filters, "sale", -1.0)
        dpp_in, ppn_in = self._ppn_totals(filters, "purchase", 1.0)
        net = ppn_out - ppn_in

        return [
            {"type": "header", "uraian": "I. Penyerahan Barang/Jasa terutang PPN & PPN Keluaran"},
            {"uraian": "Jumlah DPP Penyerahan (Keluaran)", "dpp": dpp_out, "ppn": 0.0},
            {"uraian": "Jumlah PPN Keluaran", "dpp": 0.0, "ppn": ppn_out},
            {"type": "header", "uraian": "II. Perolehan Barang/Jasa & PPN Masukan dapat dikreditkan"},
            {"uraian": "Jumlah DPP Perolehan (Masukan)", "dpp": dpp_in, "ppn": 0.0},
            {"uraian": "Jumlah PPN Masukan dapat dikreditkan", "dpp": 0.0, "ppn": ppn_in},
            {
                "type": "grand_total",
                "uraian": "III. PPN Kurang/(Lebih) Bayar (Keluaran - Masukan)",
                "dpp": 0.0,
                "ppn": net,
            },
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        row = start_row
        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col.get("header", ""), fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1
        for line in ctx.get("lines", []):
            ltype = line.get("type")
            if ltype == "header":
                sheet.merge_range(row, 0, row, 2, line.get("uraian") or "", fmts["section"])
                row += 1
                continue
            is_total = ltype == "grand_total"
            uraian_fmt = fmts["total_text"] if is_total else fmts["text"]
            num_fmt = fmts["total_num"] if is_total else fmts["num"]
            sheet.write(row, 0, line.get("uraian") or "", uraian_fmt)
            sheet.write_number(row, 1, float(line.get("dpp") or 0.0), num_fmt)
            sheet.write_number(row, 2, float(line.get("ppn") or 0.0), num_fmt)
            row += 1
        return row
