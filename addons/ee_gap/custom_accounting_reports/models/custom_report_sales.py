# -*- coding: utf-8 -*-
"""Sales register over customer invoices **and** POS orders.

One row per sold line, with an optional grouping (customer / product /
month) that adds subtotal rows. The native Sales Analysis pivot is left
as-is; this is the single curated template requested for the report menu.

Retail revenue never passes through a customer invoice: a POS order only
becomes one when the cashier explicitly flags it against a customer, and on
prd_retail_begbal all 16,064 orders are anonymous walk-in sales whose revenue
reaches the GL through the 860 session closing entries instead. Reading
``account.move`` alone therefore returned nothing at all for the apparel brand — the
client's "Sales report belum menampilkan data apapun". POS lines are now a
second source; a POS order that *was* invoiced is skipped so its invoice is
not counted twice.

Amounts use the document-currency ``price_subtotal`` / ``price_total``
(equal to the company currency for single-currency books).
"""

from datetime import date as date_cls, datetime, time
from itertools import groupby

from odoo import _, models


# POS states that represent a completed sale.
_POS_SOLD_STATES = ("paid", "done", "invoiced")


class CustomReportSales(models.AbstractModel):
    _name = "custom.report.sales"
    _inherit = "custom.report.engine"
    _description = "Custom Sales Register"

    _report_code = "sales"
    _report_title = "Sales Report"

    def _xlsx_columns(self):
        return [
            {"header": "Date", "field": "date", "kind": "date", "width": 12},
            {"header": "Invoice No", "field": "invoice_no", "kind": "text", "width": 18},
            {"header": "Customer", "field": "customer", "kind": "text", "width": 28},
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
        if group_by == "customer":
            return row["customer"] or "—"
        if group_by == "product":
            return row["product"] or "—"
        if group_by == "month":
            return row["date"].strftime("%Y-%m") if row["date"] else "—"
        return None

    def _gl_rows(self, filters):
        """Revenue read straight off the income accounts.

        the tenant booked its sales as opening-balance journal entries, neither as
        customer invoices nor through POS: on prd_tenant Rp585,585,585 of the
        Rp735,585,585 total revenue sits in a single ``entry``
        (MISC/2026/05/0001), so the document-based register showed almost
        nothing — sheet item EO #10, "sales report kosong sedangkan transaksi
        ada di detail beginning balance".

        This is an alternative *basis*, chosen in the wizard, not a third source
        stacked on the other two: an invoice's own revenue line already sits on
        an income account, so summing both would double-count every tenant that
        invoices normally and would break the exact tie to GL revenue the
        document basis gives the apparel brand.
        """
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("account_id.account_type", "=", "income"),
        ]
        if filters.get("posted_only", True):
            domain.append(("parent_state", "=", "posted"))
        else:
            domain.append(("parent_state", "in", ("draft", "posted")))
        if filters.get("partner_ids"):
            domain.append(("move_id.partner_id", "in", filters["partner_ids"]))

        rows = []
        for ml in self.env["account.move.line"].search(domain, order="date, move_id"):
            # Credit raises revenue; a debit is a reversal or a reclass out.
            amount = (ml.credit or 0.0) - (ml.debit or 0.0)
            if not amount:
                continue
            rows.append(
                {
                    "date": ml.date,
                    "invoice_no": ml.move_id.name or "",
                    "customer": ml.partner_id.display_name or ml.move_id.partner_id.display_name or "",
                    "product": ml.product_id.display_name or "",
                    "label": ml.name or "",
                    "quantity": ml.quantity or 0.0,
                    "price_unit": 0.0,
                    "discount": 0.0,
                    "untaxed": amount,
                    # Tax never sits on the revenue line itself, so there is
                    # nothing to split out and Total equals Untaxed here.
                    "tax": 0.0,
                    "total": amount,
                }
            )
        return rows

    def _pos_rows(self, filters):
        """Sold POS lines, shaped like the invoice rows."""
        if "pos.order.line" not in self.env:
            return []
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("order_id.state", "in", _POS_SOLD_STATES),
            # date_order is a Datetime: bounding it with a bare Date would
            # resolve date_to to midnight and drop the last day's sales.
            ("order_id.date_order", ">=", datetime.combine(filters["date_from"], time.min)),
            ("order_id.date_order", "<=", datetime.combine(filters["date_to"], time.max)),
            # Already invoiced -> the invoice side above reports it.
            ("order_id.account_move", "=", False),
        ]
        if filters.get("partner_ids"):
            domain.append(("order_id.partner_id", "in", filters["partner_ids"]))

        rows = []
        for pl in self.env["pos.order.line"].search(domain):
            order = pl.order_id
            untaxed = pl.price_subtotal or 0.0
            total = pl.price_subtotal_incl or 0.0
            rows.append(
                {
                    # Refund lines already carry a negative qty, so no sign flip.
                    "date": order.date_order.date() if order.date_order else None,
                    "invoice_no": order.pos_reference or order.name or "",
                    "customer": order.partner_id.display_name or _("Walk-in"),
                    "product": pl.product_id.display_name or "",
                    "label": pl.full_product_name or pl.name or "",
                    "quantity": pl.qty or 0.0,
                    "price_unit": pl.price_unit or 0.0,
                    "discount": pl.discount or 0.0,
                    "untaxed": untaxed,
                    "tax": total - untaxed,
                    "total": total,
                }
            )
        return rows

    def _build_lines(self, filters):
        group_by = filters.get("group_by") or "none"
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("date", ">=", filters["date_from"]),
            ("date", "<=", filters["date_to"]),
            ("move_id.move_type", "in", ("out_invoice", "out_refund")),
            ("display_type", "=", "product"),
        ]
        if filters.get("posted_only", True):
            domain.append(("parent_state", "=", "posted"))
        else:
            domain.append(("parent_state", "in", ("draft", "posted")))
        if filters.get("partner_ids"):
            domain.append(("move_id.partner_id", "in", filters["partner_ids"]))

        # GL basis replaces the document basis entirely — see _gl_rows.
        if (filters.get("basis") or "document") == "gl":
            rows = self._gl_rows(filters)
            return self._assemble(rows, group_by)

        rows = []
        for ml in self.env["account.move.line"].search(domain):
            sign = -1.0 if ml.move_id.move_type == "out_refund" else 1.0
            untaxed = ml.price_subtotal * sign
            total = ml.price_total * sign
            rows.append(
                {
                    "date": ml.date,
                    "invoice_no": ml.move_id.name or "",
                    "customer": ml.move_id.partner_id.display_name or "",
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

        rows += self._pos_rows(filters)
        return self._assemble(rows, group_by)

    def _assemble(self, rows, group_by):
        """Sort/group rows and append subtotal + grand-total lines."""
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
            label_field = {"customer": "customer", "product": "product"}.get(group_by, "invoice_no")
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
