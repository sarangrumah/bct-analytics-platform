# -*- coding: utf-8 -*-
"""Vendor bill vs payment mapping ("report payment bill").

Answers both halves of what AP asked for in one sheet — sheet items #5 ("report
yang menampilkan mapping antara vendor bill dengan pasangan payment numbernya")
and #13 ("report payment bill"), which are the same request:

* which payment settled which bill, and for how much — one row per allocation,
  read from ``account.partial.reconcile`` so a bill paid in instalments shows
  each instalment;
* which bills are still open — a bill with no allocation yet gets one row with
  status "Belum dibayar", so the report doubles as an unpaid-bill list instead
  of silently omitting them.

A counterpart that is not a payment (a credit note, or a manual reconciliation
against another entry) is still shown, labelled by its own document type, rather
than dropped.
"""

from __future__ import annotations

from odoo import _, models


class CustomReportBillPayment(models.AbstractModel):
    _name = "custom.report.bill.payment"
    _inherit = "custom.report.engine"
    _description = "Vendor Bill vs Payment Mapping"

    _report_code = "bill_payment"
    _report_title = "Mapping Vendor Bill vs Payment"

    def _xlsx_columns(self):
        return [
            {"header": "No. Bill", "field": "bill_no", "kind": "text", "width": 24},
            {"header": "Tgl Bill", "field": "bill_date", "kind": "date", "width": 12},
            {"header": "Vendor", "field": "partner", "kind": "text", "width": 28},
            {"header": "NPWP", "field": "npwp", "kind": "text", "width": 20},
            {"header": "Nilai Bill", "field": "bill_amount", "kind": "number", "width": 18},
            {"header": "No. Payment", "field": "payment_no", "kind": "text", "width": 24},
            {"header": "Tgl Payment", "field": "payment_date", "kind": "date", "width": 12},
            {"header": "Journal", "field": "payment_journal", "kind": "text", "width": 20},
            {"header": "Metode", "field": "payment_method", "kind": "text", "width": 16},
            {"header": "Nilai Dialokasikan", "field": "allocated", "kind": "number", "width": 18},
            {"header": "Sisa Bill", "field": "residual", "kind": "number", "width": 16},
            {"header": "Status", "field": "status", "kind": "text", "width": 16},
        ]

    _STATUS = {
        "paid": "Lunas",
        "partial": "Sebagian",
        "not_paid": "Belum dibayar",
        "reversed": "Dibatalkan",
        "in_payment": "Dalam proses",
        "blocked": "Diblokir",
    }

    def _bills(self, filters):
        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("move_type", "in", ("in_invoice", "in_refund")),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
        ]
        domain.append(
            ("state", "=", "posted") if filters.get("posted_only", True) else ("state", "in", ("draft", "posted"))
        )
        if filters.get("partner_ids"):
            domain.append(("partner_id", "in", filters["partner_ids"]))
        return self.env["account.move"].search(domain, order="partner_id, invoice_date, name")

    def _counterpart_label(self, move):
        """How to name the thing that settled the bill."""
        payment = move.origin_payment_id if "origin_payment_id" in move._fields else False
        if payment:
            method = payment.payment_method_line_id.name if payment.payment_method_line_id else ""
            return move.name or "", move.date, move.journal_id.display_name or "", method or ""
        # Not a payment: a credit note or a manual reconciliation. Say so
        # instead of leaving the payment columns blank and unexplained.
        kind = dict(move._fields["move_type"].selection).get(move.move_type, move.move_type or "")
        return move.name or "", move.date, move.journal_id.display_name or "", kind

    def _build_lines(self, filters):
        rows = []
        g_alloc = g_residual = 0.0
        seen_bills = 0

        for bill in self._bills(filters):
            partner = bill.commercial_partner_id or bill.partner_id
            npwp = self._opt(partner, "x_custom_npwp") or self._opt(partner, "vat")
            bill_amount = bill.amount_total_signed if "amount_total_signed" in bill._fields else bill.amount_total
            residual = bill.amount_residual_signed if "amount_residual_signed" in bill._fields else bill.amount_residual
            status = self._STATUS.get(bill.payment_state, bill.payment_state or "")

            base = {
                "bill_no": bill.name or "",
                "bill_date": bill.invoice_date or bill.date,
                "partner": partner.display_name or "",
                "npwp": npwp,
                "bill_amount": bill_amount,
                "residual": residual,
                "status": status,
            }

            ap_lines = bill.line_ids.filtered(lambda l: l.account_id.account_type == "liability_payable")
            allocations = []
            for ml in ap_lines:
                for partial in ml.matched_debit_ids | ml.matched_credit_ids:
                    other = partial.debit_move_id if partial.credit_move_id.id == ml.id else partial.credit_move_id
                    if other.move_id.id == bill.id:
                        continue
                    allocations.append((partial, other.move_id))

            seen_bills += 1
            if not allocations:
                rows.append(
                    dict(base, payment_no="", payment_date=None, payment_journal="", payment_method="", allocated=0.0)
                )
                g_residual += residual
                continue

            # The bill total and its residual belong to the bill, not to each
            # allocation, so print them once and blank them on repeat rows —
            # otherwise the column totals multiply by the number of instalments.
            for idx, (partial, cp_move) in enumerate(
                sorted(allocations, key=lambda a: (a[0].max_date or a[1].date, a[0].id))
            ):
                no, pdate, journal, method = self._counterpart_label(cp_move)
                row = dict(
                    base,
                    payment_no=no,
                    payment_date=pdate,
                    payment_journal=journal,
                    payment_method=method,
                    allocated=partial.amount or 0.0,
                )
                if idx:
                    row["bill_amount"] = 0.0
                    row["residual"] = 0.0
                    row["bill_no"] = ""
                    row["bill_date"] = None
                    row["partner"] = ""
                    row["npwp"] = ""
                    row["status"] = ""
                rows.append(row)
                g_alloc += partial.amount or 0.0
            g_residual += residual

        if not rows:
            rows.append({"type": "note", "bill_no": _("Tidak ada vendor bill pada periode tersebut.")})
        rows.append(
            {
                "type": "grand_total",
                "bill_no": _("TOTAL"),
                "partner": _("%s bill", seen_bills),
                "allocated": g_alloc,
                "residual": g_residual,
            }
        )
        return rows
