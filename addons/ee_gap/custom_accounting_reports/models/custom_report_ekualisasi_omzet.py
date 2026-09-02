# -*- coding: utf-8 -*-
"""Ekualisasi Peredaran Usaha — Omzet PPN vs Buku Besar.

Compares total penyerahan menurut PPN (DPP Keluaran) against revenue recognised
in the general ledger. A non-zero selisih is what the tax team must reconcile
before filing the SPT Tahunan (uang muka, ekspor non-BKP, penyerahan non-PPN,
timing differences, etc.).
"""

from __future__ import annotations

from odoo import models


class CustomReportEkualisasiOmzet(models.AbstractModel):
    _name = "custom.report.ekualisasi.omzet"
    _inherit = "custom.report.engine"
    _description = "Ekualisasi Omzet (PPN vs Buku Besar)"

    _report_code = "ekualisasi_omzet"
    _report_title = "Ekualisasi Peredaran Usaha (Omzet PPN vs Buku Besar)"

    def _xlsx_columns(self):
        return [
            {"header": "Uraian", "field": "uraian", "kind": "text", "width": 56},
            {"header": "Jumlah", "field": "amount", "kind": "number", "width": 24},
        ]

    def _build_lines(self, filters):
        # Omzet menurut PPN = DPP Keluaran (sale PPN base lines), positive.
        ppn_ids = self.env["custom.report.faktur.pajak"]._ppn_tax_ids("sale", filters["company_ids"])
        omzet_ppn = 0.0
        if ppn_ids:
            AML = self.env["account.move.line"]
            rows = AML._read_group(
                domain=self._base_move_line_domain(filters) + [("tax_ids", "in", ppn_ids)],
                aggregates=["balance:sum"],
            )
            omzet_ppn = -1.0 * (rows[0][0] or 0.0) if rows else 0.0

        # Omzet menurut Buku Besar = saldo akun pendapatan (credit positive).
        gl = self._sum_by_account(filters, account_domain=[("account_type", "in", ("income", "income_other"))])
        omzet_gl = sum(-(r.get("balance") or 0.0) for r in gl.values())

        selisih = omzet_gl - omzet_ppn
        return [
            {"uraian": "Omzet menurut SPT Masa PPN (DPP Penyerahan/Keluaran)", "amount": omzet_ppn},
            {"uraian": "Omzet menurut Buku Besar (akun Pendapatan)", "amount": omzet_gl},
            {"type": "grand_total", "uraian": "Selisih (perlu ditelusuri)", "amount": selisih},
        ]

    def _xlsx_body(self, sheet, ctx, columns, fmts, start_row):
        row = start_row
        for col_idx, col in enumerate(columns):
            sheet.write(row, col_idx, col.get("header", ""), fmts["header"])
        sheet.freeze_panes(row + 1, 0)
        row += 1
        for line in ctx.get("lines", []):
            is_total = line.get("type") == "grand_total"
            text_fmt = fmts["total_text"] if is_total else fmts["text"]
            num_fmt = fmts["total_num"] if is_total else fmts["num"]
            sheet.write(row, 0, line.get("uraian") or "", text_fmt)
            sheet.write_number(row, 1, float(line.get("amount") or 0.0), num_fmt)
            row += 1
        return row
