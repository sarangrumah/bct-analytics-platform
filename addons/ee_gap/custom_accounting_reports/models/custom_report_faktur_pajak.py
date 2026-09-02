# -*- coding: utf-8 -*-
"""Rekap Faktur Pajak — Keluaran (Output) / Masukan (Input).

One row per posted invoice/refund that carries VAT (PPN), summarising the
Dasar Pengenaan Pajak (DPP) and the PPN amount per faktur. This is the tax
team's masa working paper that feeds the SPT Masa PPN 1111 lampiran and can
be cross-checked against the e-Faktur upload.

``filters['faktur_type']``:
    * ``keluaran`` (default) — customer invoices/refunds (output VAT).
    * ``masukan``            — vendor bills/refunds (input VAT).

The e-Faktur enrichment columns (NSFP, kode status, tanggal faktur pajak,
NPWP/PKP of the counterparty) are read **defensively**: they come from the
optional ``custom_coretax`` / ``custom_tax_id`` modules and are simply blank
when those modules are not installed — mirroring how ``custom.report.tax``
cross-references Coretax without hard-depending on it.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportFakturPajak(models.AbstractModel):
    _name = "custom.report.faktur.pajak"
    _inherit = "custom.report.engine"
    _description = "Rekap Faktur Pajak (PPN Keluaran / Masukan)"

    _report_code = "faktur_pajak"
    _report_title = "Rekap Faktur Pajak"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _ppn_tax_ids(self, type_tax_use, company_ids):
        """VAT (PPN) taxes on the given side, excluding PPh withholding.

        Convention (shared with ``custom.report.tax._classify``): a tax whose
        name starts with ``PPH`` is a withholding, never PPN.
        """
        taxes = self.env["account.tax"].search(
            [
                ("type_tax_use", "=", type_tax_use),
                ("company_id", "in", list(company_ids) or [self.env.company.id]),
            ]
        )
        return taxes.filtered(lambda t: not (t.name or "").upper().startswith("PPH")).ids

    # ------------------------------------------------------------------
    # XLSX layout (generic flat body renders these)
    # ------------------------------------------------------------------
    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "invoice_no", "kind": "text", "width": 18},
            {"header": "NSFP / No. Faktur Pajak", "field": "nsfp", "kind": "text", "width": 24},
            {"header": "Kode", "field": "kode", "kind": "text", "width": 8},
            {"header": "NPWP/NIK", "field": "npwp", "kind": "text", "width": 20},
            {"header": "Nama Lawan Transaksi", "field": "partner", "kind": "text", "width": 30},
            {"header": "PKP", "field": "pkp", "kind": "text", "width": 8},
            {"header": "DPP", "field": "dpp", "kind": "number", "width": 18},
            {"header": "PPN", "field": "ppn", "kind": "number", "width": 16},
            {"header": "Total", "field": "total", "kind": "number", "width": 18},
            {"header": "Status", "field": "status", "kind": "text", "width": 16},
        ]

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _build_lines(self, filters):
        faktur_type = filters.get("faktur_type") or "keluaran"
        if faktur_type == "masukan":
            move_types = ("in_invoice", "in_refund")
            type_tax_use = "purchase"
            sign = 1.0  # vendor base line is a debit (positive balance)
        else:
            faktur_type = "keluaran"
            move_types = ("out_invoice", "out_refund")
            type_tax_use = "sale"
            sign = -1.0  # customer base line is a credit (negative balance)

        ppn_ids = self._ppn_tax_ids(type_tax_use, filters["company_ids"])
        AML = self.env["account.move.line"]
        base_domain = self._base_move_line_domain(filters)
        base_domain += [("move_id.move_type", "in", move_types)]

        rows = []
        grand_dpp = grand_ppn = grand_total = 0.0

        if ppn_ids:
            # DPP: base lines that bear a PPN tax, per move.
            dpp_rows = AML._read_group(
                domain=base_domain + [("tax_ids", "in", ppn_ids)],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            dpp_by_move = {m.id: sign * (b or 0.0) for m, b in dpp_rows}

            # PPN: tax lines whose tax IS a PPN tax, per move.
            ppn_rows = AML._read_group(
                domain=base_domain + [("tax_line_id", "in", ppn_ids)],
                groupby=["move_id"],
                aggregates=["balance:sum"],
            )
            ppn_by_move = {m.id: sign * (b or 0.0) for m, b in ppn_rows}

            move_ids = sorted(set(dpp_by_move) | set(ppn_by_move))
            moves = self.env["account.move"].browse(move_ids)
            for move in moves.sorted(lambda m: (m.invoice_date or m.date or date_cls.min, m.name or "")):
                partner = move.commercial_partner_id or move.partner_id
                dpp = dpp_by_move.get(move.id, 0.0)
                ppn = ppn_by_move.get(move.id, 0.0)
                total = dpp + ppn
                pkp = self._opt(partner, "x_custom_pkp", False)
                rows.append(
                    {
                        "date": move.invoice_date or move.date,
                        "invoice_no": move.name or "",
                        "nsfp": self._opt(move, "x_custom_nsfp"),
                        "kode": self._opt(move, "x_custom_coretax_status_code") if faktur_type == "keluaran" else "",
                        "npwp": self._opt(partner, "x_custom_npwp") or self._opt(partner, "x_custom_nik"),
                        "partner": partner.display_name or "",
                        "pkp": "PKP" if pkp else ("Non-PKP" if partner else ""),
                        "dpp": dpp,
                        "ppn": ppn,
                        "total": total,
                        "status": self._status_label(move),
                    }
                )
                grand_dpp += dpp
                grand_ppn += ppn
                grand_total += total

        rows.append(
            {
                "type": "grand_total",
                "label": "TOTAL",
                "invoice_no": "TOTAL",
                "dpp": grand_dpp,
                "ppn": grand_ppn,
                "total": grand_total,
            }
        )
        return rows

    def _status_label(self, move):
        """Human-readable Coretax status, defaulting to the accounting state."""
        raw = self._opt(move, "x_custom_coretax_status")
        mapping = {
            "draft": "Belum submit",
            "submitted": "Submitted",
            "approved": "Approved DJP",
            "rejected_djp": "Rejected DJP",
        }
        if raw:
            return mapping.get(raw, raw)
        return dict(move._fields["state"].selection).get(move.state, move.state or "")
