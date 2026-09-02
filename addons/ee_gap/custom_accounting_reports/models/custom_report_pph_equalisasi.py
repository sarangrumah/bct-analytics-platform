# -*- coding: utf-8 -*-
"""Ekualisasi Biaya vs Objek Pemotongan PPh.

Scans vendor-bill lines whose product carries a PPh withholding category and
flags those that were NOT withheld — the classic audit-defense check ("biaya
objek PPh yang belum dipotong"). Depends on
``product.template.x_custom_withholding_category_id`` and
``account.move.withholding.line`` (``custom_tax_id``); degrades to a note when
absent.
"""

from __future__ import annotations

from datetime import date as date_cls

from odoo import models


class CustomReportPphEqualisasi(models.AbstractModel):
    _name = "custom.report.pph.equalisasi"
    _inherit = "custom.report.engine"
    _description = "Ekualisasi Biaya vs Objek Pemotongan PPh"

    _report_code = "pph_equalisasi"
    _report_title = "Ekualisasi Biaya vs Objek Pemotongan PPh"

    def _xlsx_columns(self):
        return [
            {"header": "Tanggal", "field": "date", "kind": "date", "width": 12},
            {"header": "No. Dokumen", "field": "doc_no", "kind": "text", "width": 18},
            {"header": "No. Dokumen Jurnal", "field": "journal_no", "kind": "text", "width": 18},
            {"header": "Lawan Transaksi", "field": "partner", "kind": "text", "width": 26},
            {"header": "NPWP", "field": "npwp", "kind": "text", "width": 20},
            {"header": "Produk", "field": "product", "kind": "text", "width": 24},
            {"header": "COA Expense", "field": "coa_expense", "kind": "text", "width": 30},
            {"header": "Kategori PPh", "field": "kategori", "kind": "text", "width": 22},
            {"header": "DPP", "field": "dpp", "kind": "number", "width": 18},
            {"header": "Nilai PPN", "field": "ppn", "kind": "number", "width": 18},
            {"header": "Status", "field": "status", "kind": "text", "width": 16},
            {"header": "PPh Dipotong", "field": "pph", "kind": "number", "width": 18},
        ]

    @staticmethod
    def _is_pph_tax(tax):
        """Whether ``tax`` is a withholding tax rather than a VAT one.

        Vendor-bill lines here carry both: "PPN 12% (Excluded)" sits in a
        *Non-luxury Good Taxes* group while PPh 23 / 4(2) / 21 sit in groups
        named "PPh ...". Keying on the group name rather than on the sign keeps
        a 0%-rated VAT in the PPN column, where a sign test would drop it.
        """
        return (tax.tax_group_id.name or "").strip().lower().startswith("pph")

    def _line_tax_amount(self, ml, taxes):
        """Signed tax amount that ``taxes`` produce on line ``ml``."""
        if not taxes:
            return 0.0
        # price_subtotal is already net of discount; recompute from the net unit
        # price so compute_all applies each tax's own stored rate (the "12%"
        # non-luxury tax is stored as 11% — DPP nilai lain 11/12 — so hardcoding
        # a rate here would be wrong).
        price = (ml.price_unit or 0.0) * (1.0 - (ml.discount or 0.0) / 100.0)
        res = taxes.compute_all(
            price,
            currency=ml.currency_id or ml.company_currency_id,
            quantity=ml.quantity or 0.0,
            product=ml.product_id,
            partner=ml.move_id.partner_id,
        )
        return sum(t.get("amount", 0.0) for t in res.get("taxes", []))

    def _line_ppn(self, ml):
        """VAT charged on one expense line, excluding any PPh on the same line."""
        return self._line_tax_amount(ml, ml.tax_ids.filtered(lambda t: not self._is_pph_tax(t)))

    def _line_pph_from_tax(self, ml):
        """PPh withheld on the line through a native tax rather than the engine.

        Returned positive: PPh taxes are stored with a negative rate (PPh 23 is
        -2%), so the raw computation comes back negative.
        """
        return abs(self._line_tax_amount(ml, ml.tax_ids.filtered(self._is_pph_tax)))

    def _build_lines(self, filters):
        Template = self.env["product.template"]
        if "x_custom_withholding_category_id" not in Template._fields:
            return [
                {"type": "note", "doc_no": "Modul PPh (custom_tax_id) belum terpasang — tidak ada data."},
                {"type": "grand_total", "doc_no": "TOTAL", "dpp": 0.0, "pph": 0.0},
            ]

        AML = self.env["account.move.line"]
        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")),
            ("display_type", "=", "product"),
        ]
        if filters.get("posted_only", True):
            domain.append(("parent_state", "=", "posted"))
        else:
            domain.append(("parent_state", "in", ("draft", "posted")))

        # An expense line counts as "objek PPh" through any of three routes.
        # Keying on the product mapping alone used to miss almost everything:
        # bills are commonly keyed as free-text expense lines with no product at
        # all (on prd_retail_begbal all 247 bill lines have product_id NULL, and
        # 1 of 31,978 templates is mapped), so the 113 lines actually carrying a
        # PPh tax were invisible to this report.
        routes = [("product_id.product_tmpl_id.x_custom_withholding_category_id", "!=", False)]
        if "x_custom_withholding_category_id" in AML._fields:
            # Kode Objek PPh picked directly on the line (custom_tax_id).
            routes.append(("x_custom_withholding_category_id", "!=", False))
        # A native PPh tax on the line — the PPh groups are named "PPh ...",
        # which also keeps a 0%-rated VAT out of this test.
        routes.append(("tax_ids.tax_group_id.name", "=ilike", "pph%"))
        domain += ["|"] * (len(routes) - 1) + routes

        lines = AML.search(domain)

        # Which of these lines were actually withheld?
        withheld = {}
        if "account.move.withholding.line" in self.env and lines:
            wls = self.env["account.move.withholding.line"].sudo().search([("move_line_id", "in", lines.ids)])
            for wl in wls:
                withheld[wl.move_line_id.id] = withheld.get(wl.move_line_id.id, 0.0) + (wl.tax_amount or 0.0)

        rows = []
        g_dpp = g_ppn = g_pph = 0.0
        belum = 0
        for ml in lines.sorted(lambda l: (l.date or date_cls.min, l.move_id.name or "")):
            move = ml.move_id
            partner = move.commercial_partner_id or move.partner_id
            template = ml.product_id.product_tmpl_id
            # Kode objek: from the line's own picker first (that is where the
            # operator sets it), then the product mapping.
            category = self._opt(ml, "x_custom_withholding_category_id", False) or self._opt(
                template, "x_custom_withholding_category_id", False
            )
            dpp = ml.price_subtotal or 0.0
            ppn = self._line_ppn(ml)
            # Engine-booked PPh first; fall back to a native PPh tax on the line,
            # otherwise a line withheld via tax would be reported as "BELUM
            # dipotong" — the opposite of the truth.
            pph = withheld.get(ml.id, 0.0)
            via_tax = ""
            if not pph:
                pph = self._line_pph_from_tax(ml)
                if pph:
                    via_tax = " (tax)"
            is_withheld = bool(pph)
            if not is_withheld:
                belum += 1
            pph_taxes = ml.tax_ids.filtered(self._is_pph_tax)
            kategori = category.name if category else (", ".join(pph_taxes.mapped("name")) if pph_taxes else "")
            # The separate "Pemotongan PPh" entry, when custom_tax_id books one.
            wmove = self._opt(move, "x_custom_withholding_move_id", False)
            exp_acc = ml.account_id
            rows.append(
                {
                    "date": ml.date,
                    "doc_no": move.name or "",
                    "journal_no": wmove.name if wmove else "",
                    "partner": partner.display_name or "",
                    "npwp": self._opt(partner, "x_custom_npwp") or self._opt(partner, "vat"),
                    "product": template.display_name or "",
                    "coa_expense": (
                        ("%s %s" % (self._account_code(exp_acc), exp_acc.name or "")).strip() if exp_acc else ""
                    ),
                    "kategori": kategori,
                    "dpp": dpp,
                    "ppn": ppn,
                    "status": ("Dipotong" + via_tax) if is_withheld else "BELUM dipotong",
                    "pph": pph,
                }
            )
            g_dpp += dpp
            g_ppn += ppn
            g_pph += pph

        rows.append(
            {
                "type": "grand_total",
                "doc_no": "TOTAL",
                "partner": "%d baris objek PPh, %d BELUM dipotong" % (len(rows), belum),
                "dpp": g_dpp,
                "ppn": g_ppn,
                "pph": g_pph,
            }
        )
        return rows
