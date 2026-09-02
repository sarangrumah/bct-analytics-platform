# -*- coding: utf-8 -*-
"""Monitoring Angsuran PPh 25 (TAX-CR-02).

PPh 25 is the monthly corporate-income-tax installment (angsuran), booked as a
prepaid asset ("PPh 25 Dibayar di Muka / Angsuran", e.g. PSAK account 11630)
that is later credited against the annual PPh Badan. There is no dedicated
installment model, so the report reads the movements on that prepaid account
directly: each debit is an angsuran paid, each credit a kompensasi.

The account is auto-detected by name (like the Uang Muka / Advance report). If
none is found the report emits an informational note.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportPph25(models.AbstractModel):
    _name = "custom.report.pph25"
    _inherit = "custom.report.engine"
    _description = "Monitoring Angsuran PPh 25"

    _report_code = "pph25"
    _report_title = "Monitoring Angsuran PPh 25"

    def _pph25_accounts(self, company_ids):
        return self.env["account.account"].search(
            [
                ("account_type", "in", ("asset_current", "asset_non_current")),
                "|",
                ("name", "ilike", "pph 25"),
                ("name", "ilike", "angsuran pph"),
                ("company_ids", "in", list(company_ids)),
            ]
        )

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "Keterangan", "field": "label", "kind": "text", "width": 40},
            {"header": "Angsuran Dibayar", "field": "debit", "kind": "number", "width": 18},
            {"header": "Kompensasi/Kredit", "field": "credit", "kind": "number", "width": 18},
            {"header": "Saldo", "field": "saldo", "kind": "number", "width": 18},
        ]

    def _build_lines(self, filters):
        accounts = self._pph25_accounts(filters["company_ids"])
        if not accounts:
            return [
                {
                    "type": "note",
                    "doc_no": "Akun PPh 25 tidak ditemukan — buat akun bernama "
                    "'PPh 25 Dibayar di Muka' / 'Angsuran PPh 25' (asset).",
                },
                {"type": "grand_total", "doc_no": "TOTAL", "debit": 0.0, "credit": 0.0},
            ]

        AML = self.env["account.move.line"]
        domain = self._base_move_line_domain(filters) + [("account_id", "in", accounts.ids)]
        move_lines = AML.search(domain, order="date, id")

        rows = []
        running = g_debit = g_credit = 0.0
        for ml in move_lines.sorted(lambda l: (l.date or date_cls.min, l.id)):
            running += (ml.debit or 0.0) - (ml.credit or 0.0)
            rows.append(
                {
                    "date": ml.date,
                    "doc_no": ml.move_id.name or "",
                    "label": ml.name or "",
                    "debit": ml.debit or 0.0,
                    "credit": ml.credit or 0.0,
                    "saldo": running,
                }
            )
            g_debit += ml.debit or 0.0
            g_credit += ml.credit or 0.0

        rows.append({"type": "grand_total", "doc_no": "TOTAL", "debit": g_debit, "credit": g_credit, "saldo": running})
        return rows
