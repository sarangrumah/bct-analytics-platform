# -*- coding: utf-8 -*-
"""Purchase register over posted vendor bills.

One row per bill product line (in_invoice / in_refund, refunds signed
negative), with an optional grouping (vendor / product / month) that adds
subtotal rows. The native Purchase Analysis pivot is left as-is; this is the
single curated template requested for the report menu — the vendor-bill mirror
of :mod:`custom_report_sales`.

Amounts use the document-currency ``price_subtotal`` / ``price_total`` (equal
to the company currency for single-currency books).
"""

from datetime import date as date_cls
from itertools import groupby

from odoo import models


class CustomReportPurchase(models.AbstractModel):
    _name = "custom.report.purchase"
    _inherit = "custom.report.engine"
    _description = "Custom Purchase Register"

    _report_code = "purchase"
    _report_title = "Purchase Report"

    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Bill No", "field": "invoice_no", "kind": "text", "width": 18},
            {"header": "Vendor", "field": "vendor", "kind": "text", "width": 28},
            {"header": "Product", "field": "product", "kind": "text", "width": 26},
            {"header": "Description", "field": "label", "kind": "text", "width": 30},
            {"header": "Qty", "field": "quantity", "kind": "number", "width": 10},
            {"header": "Unit Price", "field": "price_unit", "kind": "number", "width": 14},
            {"header": "Disc %", "field": "discount", "kind": "number", "width": 9},
            {"header": "Untaxed", "field": "untaxed", "kind": "number", "width": 16},
            {"header": "Tax", "field": "tax", "kind": "number", "width": 14},
            {"header": "Total", "field": "total", "kind": "number", "width": 16},
        ]

    # The engine's generic flat ``_xlsx_body`` already renders these lines
    # (subtotal / grand_total rows are emitted bold).

    def _group_key(self, row, group_by):
        if group_by == "vendor":
            return row["vendor"] or "—"
        if group_by == "product":
            return row["product"] or "—"
        if group_by == "month":
            return row["date"].strftime("%Y-%m") if row["date"] else "—"
        return None

    def _build_lines(self, filters):
        group_by = filters.get("group_by") or "none"
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")),
            ("display_type", "=", "product"),
        ]
        if filters.get("posted_only", True):
            domain.append(("parent_state", "=", "posted"))
        else:
            domain.append(("parent_state", "in", ("draft", "posted")))
        if filters.get("partner_ids"):
            domain.append(("move_id.partner_id", "in", filters["partner_ids"]))

        rows = []
        for ml in self.env["account.move.line"].search(domain):
            sign = -1.0 if ml.move_id.move_type == "in_refund" else 1.0
            untaxed = ml.price_subtotal * sign
            total = ml.price_total * sign
            rows.append(
                {
                    "date": ml.date,
                    "invoice_no": ml.move_id.name or "",
                    "vendor": ml.move_id.partner_id.display_name or "",
                    "product": ml.product_id.display_name or "",
                    "label": ml.name or "",
                    "quantity": (ml.quantity or 0.0) * sign,
                    "price_unit": ml.price_unit or 0.0,
                    "discount": ml.discount or 0.0,
                    "untaxed": untaxed,
                    "tax": total - untaxed,
                    "total": total,
                }
            )

        lines = []
        g_qty = g_un = g_tx = g_tot = 0.0

        def _accumulate(r):
            nonlocal g_qty, g_un, g_tx, g_tot
            g_qty += r["quantity"]
            g_un += r["untaxed"]
            g_tx += r["tax"]
            g_tot += r["total"]

        if group_by == "none":
            for r in sorted(rows, key=lambda r: (r["date"] or date_cls.min, r["invoice_no"])):
                lines.append(r)
                _accumulate(r)
        else:
            label_field = {"vendor": "vendor", "product": "product"}.get(group_by, "invoice_no")
            rows.sort(key=lambda r: (self._group_key(r, group_by), r["date"] or date_cls.min, r["invoice_no"]))
            for key, grp in groupby(rows, key=lambda r: self._group_key(r, group_by)):
                grp = list(grp)
                s_qty = s_un = s_tx = s_tot = 0.0
                for r in grp:
                    lines.append(r)
                    s_qty += r["quantity"]
                    s_un += r["untaxed"]
                    s_tx += r["tax"]
                    s_tot += r["total"]
                    _accumulate(r)
                subtotal = {
                    "type": "subtotal",
                    "quantity": s_qty,
                    "untaxed": s_un,
                    "tax": s_tx,
                    "total": s_tot,
                    label_field: "Subtotal: %s" % (key or "—"),
                }
                lines.append(subtotal)

        lines.append(
            {
                "type": "grand_total",
                "invoice_no": "Grand Total",
                "quantity": g_qty,
                "untaxed": g_un,
                "tax": g_tx,
                "total": g_tot,
            }
        )
        return lines
