# -*- coding: utf-8 -*-
"""Import PPN Masukan — flat one-row-per-faktur list for input-VAT import.

The tax team imports input VAT (PPN Masukan) into their tax application from a
flat list. This report emits exactly the columns they need, per posted vendor
bill/refund that carries PPN:

    NPWP / Nama Lawan Transaksi / Tanggal Jurnal / Nomor Dokumen Jurnal /
    Tanggal Invoice / Nomor Invoice / Nilai DPP / Nilai PPN

DPP and PPN are computed exactly like ``custom.report.faktur.pajak`` (masukan
side): DPP = sum of base lines bearing a PPN tax; PPN = sum of the PPN tax
lines, both per move. The NPWP field is read defensively from ``custom_tax_id``.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportPpnMasukanImport(models.AbstractModel):
    _name = "custom.report.ppn.masukan.import"
    _inherit = "custom.report.engine"
    _description = "Import PPN Masukan"

    _report_code = "ppn_masukan_import"
    _report_title = "Import PPN Masukan"

    def _xlsx_columns(self):
        return [
            {"header": "NPWP", "field": "npwp", "kind": "text", "width": 22},
            {"header": "Nama Lawan Transaksi", "field": "partner", "kind": "text", "width": 32},
            {"header": "Tanggal Jurnal", "field": "journal_date", "kind": "date", "width": 14},
            {"header": "Nomor Dokumen Jurnal", "field": "journal_no", "kind": "text", "width": 20},
            {"header": "Tanggal Invoice", "field": "invoice_date", "kind": "date", "width": 14},
            {"header": "Nomor Invoice", "field": "invoice_no", "kind": "text", "width": 20},
            {"header": "Nilai DPP", "field": "dpp", "kind": "number", "width": 18},
            {"header": "Nilai PPN", "field": "ppn", "kind": "number", "width": 18},
        ]

    def _ppn_tax_ids(self, company_ids):
        """Purchase PPN taxes (excludes PPh withholding, by name convention)."""
        taxes = self.env["account.tax"].search(
            [
                ("type_tax_use", "=", "purchase"),
                ("company_id", "in", list(company_ids) or [self.env.company.id]),
            ]
        )
        return taxes.filtered(lambda t: not (t.name or "").upper().startswith("PPH")).ids

    def _build_lines(self, filters):
        ppn_ids = self._ppn_tax_ids(filters["company_ids"])
        AML = self.env["account.move.line"]
        base_domain = self._base_move_line_domain(filters)
        base_domain += [("move_id.move_type", "in", ("in_invoice", "in_refund"))]

        rows = []
        grand_dpp = grand_ppn = 0.0

        if ppn_ids:
            dpp_rows = AML._read_group(
                domain=base_domain + [("tax_ids", "in", ppn_ids)],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            dpp_by_move = {m.id: (b or 0.0) for m, b in dpp_rows}

            ppn_rows = AML._read_group(
                domain=base_domain + [("tax_line_id", "in", ppn_ids)],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            ppn_by_move = {m.id: (b or 0.0) for m, b in ppn_rows}

            move_ids = sorted(set(dpp_by_move) | set(ppn_by_move))
            moves = self.env["account.move"].browse(move_ids)
            for move in moves.sorted(lambda m: (m.date or m.invoice_date or date_cls.min, m.name or "")):
                partner = move.commercial_partner_id or move.partner_id
                dpp = dpp_by_move.get(move.id, 0.0)
                ppn = ppn_by_move.get(move.id, 0.0)
                rows.append(
                    {
                        "npwp": self._opt(partner, "x_custom_npwp") or self._opt(partner, "x_custom_nik"),
                        "partner": partner.display_name or "",
                        "journal_date": move.date,
                        "journal_no": move.name or "",
                        "invoice_date": move.invoice_date,
                        "invoice_no": move.ref or "",
                        "dpp": dpp,
                        "ppn": ppn,
                    }
                )
                grand_dpp += dpp
                grand_ppn += ppn

        rows.append(
            {
                "type": "grand_total",
                "partner": "TOTAL",
                "dpp": grand_dpp,
                "ppn": grand_ppn,
            }
        )
        return rows
