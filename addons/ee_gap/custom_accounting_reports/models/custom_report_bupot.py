# -*- coding: utf-8 -*-
"""Rekap Bukti Potong PPh (Unifikasi) per masa.

Reads the normalised ``custom.coretax.bukti.potong`` ledger (populated
automatically from vendor-bill withholding lines and payroll PPh 21), grouped
by jenis PPh with per-type subtotals. Two directions:

    * ``issued``   (default) — PPh WE withhold as pemotong → feeds the SPT Masa
      PPh Unifikasi / e-Bupot upload.
    * ``received``          — PPh withheld from US by counterparties → the
      kredit-pajak working paper (TAX-CR-01).

The source model lives in the optional ``custom_coretax`` module. When it is
not installed the report degrades to an informational note instead of raising,
matching the defensive posture of ``custom.report.tax``.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


# Display + iteration order for jenis PPh (Unifikasi first, PPh 21 last).
_PPH_ORDER = ["22", "23", "4_2", "15", "26", "21"]
_PPH_LABEL = {
    "21": "PPh 21",
    "22": "PPh 22",
    "23": "PPh 23",
    "4_2": "PPh 4 ayat (2)",
    "15": "PPh 15",
    "26": "PPh 26",
}


class CustomReportBupot(models.AbstractModel):
    _name = "custom.report.bupot"
    _inherit = "custom.report.engine"
    _description = "Rekap Bukti Potong PPh"

    _report_code = "bupot"
    _report_title = "Rekap Bukti Potong PPh"

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Bukti Potong", "field": "no_bupot", "kind": "text", "width": 26},
            {"header": "NPWP/NIK", "field": "npwp", "kind": "text", "width": 20},
            {"header": "Nama Pihak Dipotong", "field": "partner", "kind": "text", "width": 30},
            {"header": "Jenis PPh", "field": "jenis", "kind": "text", "width": 16},
            {"header": "DPP", "field": "dpp", "kind": "number", "width": 18},
            {"header": "Tarif (%)", "field": "tarif", "kind": "number", "width": 10},
            {"header": "PPh Dipotong", "field": "pph", "kind": "number", "width": 18},
            {"header": "Status", "field": "status", "kind": "text", "width": 14},
        ]

    def _build_lines(self, filters):
        direction = filters.get("direction") or "issued"
        pph_kind = filters.get("pph_kind") or "all"

        if "custom.coretax.bukti.potong" not in self.env:
            return [
                {
                    "type": "note",
                    "no_bupot": "Modul Bukti Potong (custom_coretax) belum terpasang — tidak ada data.",
                },
                {"type": "grand_total", "no_bupot": "TOTAL", "dpp": 0.0, "pph": 0.0},
            ]

        Bupot = self.env["custom.coretax.bukti.potong"].sudo()
        # AND(source, date>=, date<=, OR(no move, move.company in scope)).
        domain = [
            "&",
            "&",
            "&",
            ("source", "=", direction),
            ("tanggal_bupot", ">=", filters["date_from"]),
            ("tanggal_bupot", "<=", filters["date_to"]),
            "|",
            ("account_move_id", "=", False),
            ("account_move_id.company_id", "in", list(filters["company_ids"])),
        ]
        if pph_kind != "all":
            domain = ["&", ("jenis_pph", "=", pph_kind)] + domain
        records = Bupot.search(domain)

        # Bucket per jenis PPh.
        buckets = {}
        for rec in records:
            partner = rec.partner_id.commercial_partner_id or rec.partner_id
            buckets.setdefault(rec.jenis_pph, []).append(
                {
                    "date": rec.tanggal_bupot,
                    "no_bupot": rec.no_bupot or "",
                    "npwp": self._opt(partner, "x_custom_npwp") or self._opt(partner, "x_custom_nik"),
                    "partner": partner.display_name or "",
                    "jenis": _PPH_LABEL.get(rec.jenis_pph, rec.jenis_pph or ""),
                    "dpp": rec.dpp or 0.0,
                    "tarif": rec.tarif or 0.0,
                    "pph": rec.pph_terpotong or 0.0,
                    "status": dict(rec._fields["state"].selection).get(rec.state, rec.state or ""),
                }
            )

        lines = []
        g_dpp = g_pph = 0.0
        ordered_kinds = [k for k in _PPH_ORDER if k in buckets] + [k for k in buckets if k not in _PPH_ORDER]
        for kind in ordered_kinds:
            group = sorted(buckets[kind], key=lambda r: (r["date"] or date_cls.min, r["no_bupot"]))
            s_dpp = s_pph = 0.0
            for row in group:
                lines.append(row)
                s_dpp += row["dpp"]
                s_pph += row["pph"]
            lines.append(
                {
                    "type": "subtotal",
                    "no_bupot": "Subtotal %s" % _PPH_LABEL.get(kind, kind),
                    "dpp": s_dpp,
                    "pph": s_pph,
                }
            )
            g_dpp += s_dpp
            g_pph += s_pph

        lines.append(
            {
                "type": "grand_total",
                "no_bupot": "TOTAL",
                "dpp": g_dpp,
                "pph": g_pph,
            }
        )
        return lines
