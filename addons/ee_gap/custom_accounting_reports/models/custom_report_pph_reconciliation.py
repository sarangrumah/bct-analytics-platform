# -*- coding: utf-8 -*-
"""Rekonsiliasi PPh Terutang vs Disetor (TAX-REC-03).

Per Hutang-PPh liability account: opening balance, PPh *terutang* recognised in
the masa (credits — booked by the withholding GL entry from ``custom_tax_id``),
PPh *disetor* (debits — the setoran/NTPN journal), and the closing balance =
what is still owed to DJP. A non-zero closing that should be zero flags an
unremitted or misposted PPh liability.

Requires the withholding GL posting (``custom_tax_id.withholding_gl_posting``)
to be enabled so the terutang side actually hits the ledger.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta

from odoo import models


class CustomReportPphReconciliation(models.AbstractModel):
    _name = "custom.report.pph.reconciliation"
    _inherit = "custom.report.engine"
    _description = "Rekonsiliasi PPh Terutang vs Disetor"

    _report_code = "pph_reconciliation"
    _report_title = "Rekonsiliasi PPh Terutang vs Disetor"

    def _hutang_pph_domain(self):
        """Liability accounts that look like a 'Hutang/Utang PPh' payable."""
        return [("account_type", "=", "liability_current"), ("name", "ilike", "pph")]

    def _xlsx_columns(self):
        return [
            {"header": "Akun Hutang PPh", "field": "account", "kind": "text", "width": 40},
            {"header": "Saldo Awal", "field": "saldo_awal", "kind": "number", "width": 18},
            {"header": "Terutang (masa)", "field": "terutang", "kind": "number", "width": 18},
            {"header": "Disetor (masa)", "field": "disetor", "kind": "number", "width": 18},
            {"header": "Saldo Akhir (Belum Disetor)", "field": "saldo_akhir", "kind": "number", "width": 24},
        ]

    def _build_lines(self, filters):
        acc_domain = self._hutang_pph_domain()
        period = self._sum_by_account(filters, account_domain=acc_domain)
        opening_filters = dict(
            filters,
            date_from=date_cls(1970, 1, 1),
            date_to=filters["date_from"] - timedelta(days=1),
        )
        opening = self._sum_by_account(opening_filters, account_domain=acc_domain)

        rows = []
        g_awal = g_ter = g_dis = g_akhir = 0.0
        for aid in sorted(set(period) | set(opening)):
            p = period.get(aid, {})
            o = opening.get(aid, {})
            # Liability: credit-normal -> present balances as credit-positive.
            saldo_awal = -(o.get("balance", 0.0))
            terutang = p.get("credit", 0.0)
            disetor = p.get("debit", 0.0)
            saldo_akhir = saldo_awal + terutang - disetor
            code = p.get("account_code") or o.get("account_code") or ""
            name = p.get("account_name") or o.get("account_name") or ""
            rows.append(
                {
                    "account": ("%s %s" % (code, name)).strip(),
                    "saldo_awal": saldo_awal,
                    "terutang": terutang,
                    "disetor": disetor,
                    "saldo_akhir": saldo_akhir,
                }
            )
            g_awal += saldo_awal
            g_ter += terutang
            g_dis += disetor
            g_akhir += saldo_akhir

        if not rows:
            rows.append(
                {
                    "type": "note",
                    "account": "Tidak ada akun 'Hutang PPh' (liability_current). "
                    "Aktifkan withholding GL posting & pastikan akun hutang PPh ada.",
                }
            )
        rows.append(
            {
                "type": "grand_total",
                "account": "TOTAL",
                "saldo_awal": g_awal,
                "terutang": g_ter,
                "disetor": g_dis,
                "saldo_akhir": g_akhir,
            }
        )
        return rows
