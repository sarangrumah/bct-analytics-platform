# -*- coding: utf-8 -*-
"""GL Open Items / Outstanding Balance.

Every unsettled line on a reconcilable account **as of** a cut-off date,
grouped per account. Covers AR and AP but also the clearing accounts that
neither aged report reaches — GR/IR, advances, intercompany suspense — which is
what "GL open items" means to Finance.

Deliberately different from Aged Receivable/Payable: those read the *current*
``amount_residual``, so a line settled after the cut-off already appears
reduced. Here the residual is rebuilt from the reconciliations that had actually
happened by ``date_to`` (``account.partial.reconcile.max_date``), which is the
only version that ties to the ledger at period end.
"""

from __future__ import annotations

from odoo import _, models


class CustomReportGlOpenItems(models.AbstractModel):
    _name = "custom.report.gl.open.items"
    _inherit = "custom.report.engine"
    _description = "GL Open Items / Outstanding Balance"

    _report_code = "gl_open_items"
    _report_title = "GL Open Items / Outstanding Balance"

    def _xlsx_columns(self):
        return [
            {"header": "Akun", "field": "account", "kind": "text", "width": 34},
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 20},
            {"header": "Referensi", "field": "reference", "kind": "text", "width": 20},
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 28},
            {"header": "Jatuh Tempo", "field": "due_date", "kind": "date", "width": 12},
            {"header": "Umur (hari)", "field": "age", "kind": "number", "width": 11},
            {"header": "Debit", "field": "debit", "kind": "number", "width": 16},
            {"header": "Kredit", "field": "credit", "kind": "number", "width": 16},
            {"header": "Outstanding", "field": "outstanding", "kind": "number", "width": 18},
        ]

    # ------------------------------------------------------------------
    # As-of residual
    # ------------------------------------------------------------------
    def _settled_by(self, lines, date_to):
        """How much of each line was already reconciled on/before ``date_to``.

        Returns ``{line_id: signed_amount_settled}`` in company currency, signed
        the same way as ``balance`` so it can simply be subtracted. A line
        appearing on the debit side of a partial had that much of its debit
        cleared; on the credit side, its credit.

        A settlement is only counted when **both** legs are inside the reporting
        scope. prd_retail_begbal carries a partial reconcile (Rp75,405,550)
        against a move that is still *draft*: crediting the posted side for it
        would report the line as settled against something that is not in the
        books, and the report would stop tying to the ledger. Treating it as
        still open is both correct and what makes the total reconcile.
        """
        if not lines:
            return {}
        ids = set(lines.ids)
        Partial = self.env["account.partial.reconcile"].sudo()
        partials = Partial.search(
            [
                "&",
                ("max_date", "<=", date_to),
                "|",
                ("debit_move_id", "in", lines.ids),
                ("credit_move_id", "in", lines.ids),
            ]
        )
        settled = {}
        for p in partials:
            if p.debit_move_id.id not in ids or p.credit_move_id.id not in ids:
                continue
            amount = p.amount or 0.0
            settled[p.debit_move_id.id] = settled.get(p.debit_move_id.id, 0.0) + amount
            settled[p.credit_move_id.id] = settled.get(p.credit_move_id.id, 0.0) - amount
        return settled

    def _candidate_lines(self, filters):
        """Posted lines on reconcilable accounts, dated up to the cut-off.

        Reconciled lines are NOT filtered out here: one settled *after* the
        cut-off was still open then, and dropping it would understate the
        balance — the whole reason this report exists.
        """
        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("account_id.reconcile", "=", True),
            ("parent_state", "=", "posted"),
            ("date", "<=", filters["date_to"]),
        ]
        if filters.get("date_from"):
            domain.append(("date", ">=", filters["date_from"]))
        if filters.get("partner_ids"):
            domain.append(("partner_id", "in", filters["partner_ids"]))
        if filters.get("account_ids"):
            domain.append(("account_id", "in", filters["account_ids"]))
        if filters.get("account_types"):
            domain.append(("account_id.account_type", "in", filters["account_types"]))
        return self.env["account.move.line"].search(domain, order="account_id, date, id")

    def _build_lines(self, filters):
        date_to = filters["date_to"]
        lines = self._candidate_lines(filters)
        settled = self._settled_by(lines, date_to)

        rows = []
        per_account = {}
        for ml in lines:
            balance = ml.balance or 0.0
            outstanding = balance - settled.get(ml.id, 0.0)
            if self.env.company.currency_id.is_zero(outstanding):
                continue
            acc = ml.account_id
            key = acc.id
            per_account.setdefault(
                key,
                {
                    "label": ("%s %s" % (self._account_code(acc), acc.name or "")).strip(),
                    "rows": [],
                },
            )
            due = ml.date_maturity or ml.date
            per_account[key]["rows"].append(
                {
                    "account": per_account[key]["label"],
                    "date": ml.date,
                    "doc_no": ml.move_id.name or "",
                    "reference": ml.ref or ml.move_id.ref or "",
                    "partner": (ml.partner_id.display_name or ""),
                    "due_date": ml.date_maturity,
                    "age": (date_to - due).days if due else 0,
                    "debit": ml.debit or 0.0,
                    "credit": ml.credit or 0.0,
                    "outstanding": outstanding,
                }
            )

        grand = 0.0
        for key in sorted(per_account, key=lambda k: per_account[k]["label"]):
            block = per_account[key]
            subtotal = 0.0
            for row in block["rows"]:
                rows.append(row)
                subtotal += row["outstanding"]
            rows.append(
                {
                    "type": "subtotal",
                    "account": _("Subtotal %s", block["label"]),
                    "outstanding": subtotal,
                }
            )
            grand += subtotal

        if not rows:
            rows.append({"type": "note", "account": _("Tidak ada open item pada tanggal tersebut.")})
        rows.append({"type": "grand_total", "account": _("TOTAL OUTSTANDING"), "outstanding": grand})
        return rows
