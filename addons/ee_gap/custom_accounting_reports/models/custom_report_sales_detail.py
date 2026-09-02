# -*- coding: utf-8 -*-
"""Detail sales report shaped like the X24DN XStore export.

Built for reconciling XStore against Odoo line by line, and for tying sales /
COGS / margin back to the GL and TB. One row per POS order line, carrying the
mandatory columns the client listed.

Where each column comes from
----------------------------
Store code, register and transaction number are all encoded in
``pos.order.pos_reference``, which the retail import writes as
``{store}-{register}-{transaction}`` (returns as ``RET-{store}-...``). All
16,064 orders on prd_retail_begbal parse cleanly into those three parts, so the
register is recoverable even though it has no column of its own.

Amounts follow the source file's convention: POS prices are **tax-inclusive**
(``price_unit`` equals ``price_subtotal_incl`` per unit) and already net of
discount, because the retail import books the X24DN discount as a contra-revenue
reclass rather than as a line discount. ``pos.order.line.discount`` is therefore
0 on every line while the real discount sits in ``ri_src_discount`` — Rp1.23bn
for June 2026 alone — so the discount column reads the source field and the
gross is grossed back up from it:

    Discount = ri_src_discount           (X24DN, the source of truth)
    Total    = price_subtotal_incl       (tax-incl, after discount)
    Gross    = Total + Discount          (tax-incl, before discount)
    Tax      = price_subtotal_incl - price_subtotal
    Net      = price_subtotal            (the DPP that reaches revenue)

Net ties exactly to GL revenue for the period (Rp5,633,504,522.00 for June 2026
on prd_retail_begbal), which is what makes the sheet usable against the TB.

COGS uses ``qty x product.standard_price`` — deliberately the same basis as
``retail.cogs.run``, which is what actually books COGS to the GL for these
stores. Deriving it any other way would produce a margin that disagrees with
the ledger. Two consequences are surfaced rather than hidden: a product still
valued at zero cost (sold before it was ever purchased) yields COGS 0, and a
non-storable pass-through item (paper bags, services) has no cost to release —
both are flagged in the Catatan column so nobody reads the margin as real.

Margin compares like with like: net revenue excluding tax, minus COGS.
"""

from __future__ import annotations

from datetime import datetime, time

from odoo import _, models


_POS_SOLD_STATES = ("paid", "done", "invoiced")


class CustomReportSalesDetail(models.AbstractModel):
    _name = "custom.report.sales.detail"
    _inherit = "custom.report.engine"
    _description = "Sales Detail (X24DN layout)"

    _report_code = "sales_detail"
    _report_title = "Sales Report Detail (XStore X24DN)"

    def _xlsx_columns(self):
        return [
            {"header": "Store Code", "field": "store_code", "kind": "text", "width": 12},
            {"header": "Store Name", "field": "store_name", "kind": "text", "width": 30},
            {"header": "Register", "field": "register", "kind": "text", "width": 10},
            {"header": "Transaction No", "field": "txn_no", "kind": "text", "width": 15},
            {"header": "Transaction Date", "field": "txn_date", "kind": "date", "width": 14},
            {"header": "Item Code", "field": "item_code", "kind": "text", "width": 18},
            {"header": "Item Name", "field": "item_name", "kind": "text", "width": 34},
            {"header": "Product Category", "field": "categ", "kind": "text", "width": 26},
            {"header": "Qty", "field": "qty", "kind": "number", "width": 9},
            {"header": "Sales Gross", "field": "gross", "kind": "number", "width": 16},
            {"header": "Sales Discount", "field": "discount", "kind": "number", "width": 16},
            {"header": "Sales Tax", "field": "tax", "kind": "number", "width": 15},
            {"header": "Sales Total", "field": "total", "kind": "number", "width": 16},
            {"header": "Sales Net (DPP)", "field": "net", "kind": "number", "width": 16},
            {"header": "COGS", "field": "cogs", "kind": "number", "width": 16},
            {"header": "Margin", "field": "margin", "kind": "number", "width": 16},
            {"header": "Catatan", "field": "note", "kind": "text", "width": 22},
        ]

    @staticmethod
    def _split_reference(reference):
        """``{store}-{register}-{txn}`` -> the three parts.

        ``RET-`` prefixes a return; it is stripped before splitting so a return
        lands under the same store/register as the sale it reverses.
        """
        ref = (reference or "").strip()
        is_return = ref.upper().startswith("RET-")
        if is_return:
            ref = ref[4:]
        parts = ref.split("-")
        if len(parts) >= 3:
            # Only the last two fields are register + transaction; a store code
            # containing a dash keeps the remainder.
            return "-".join(parts[:-2]), parts[-2], parts[-1], is_return
        return ref, "", "", is_return

    def _pos_lines(self, filters):
        # Guarded: this shared module also ships to tenants without POS.
        if "pos.order.line" not in self.env:
            return self.env["account.move.line"].browse()
        domain = [
            ("company_id", "in", list(filters["company_ids"])),
            ("order_id.state", "in", _POS_SOLD_STATES),
            # date_order is a Datetime: a bare Date bound would resolve date_to
            # to midnight and silently drop the last trading day.
            ("order_id.date_order", ">=", datetime.combine(filters["date_from"], time.min)),
            ("order_id.date_order", "<=", datetime.combine(filters["date_to"], time.max)),
        ]
        codes = filters.get("store_codes") or []
        if codes:
            # Store code is the leading segment of pos_reference; a return is
            # prefixed with RET- so both spellings must match.
            sub = []
            for code in codes:
                sub += [
                    ("order_id.pos_reference", "=like", "%s-%%" % code),
                    ("order_id.pos_reference", "=like", "RET-%s-%%" % code),
                ]
            domain += ["|"] * (len(sub) - 1) + sub
        if filters.get("categ_ids"):
            domain.append(("product_id.categ_id", "child_of", filters["categ_ids"]))
        return self.env["pos.order.line"].search(domain, order="order_id, id")

    def _build_lines(self, filters):
        company = self.env["res.company"].browse(list(filters["company_ids"])[:1]) or self.env.company
        rows = []
        tot = {k: 0.0 for k in ("qty", "gross", "discount", "tax", "total", "net", "cogs", "margin")}
        zero_cost_qty = 0.0

        for pl in self._pos_lines(filters):
            order = pl.order_id
            store_code, register, txn_no, _is_return = self._split_reference(order.pos_reference)
            product = pl.product_id

            qty = pl.qty or 0.0
            total = pl.price_subtotal_incl or 0.0
            net = pl.price_subtotal or 0.0
            # ri_src_discount is the X24DN discount; it is absent when the
            # retail-import module is not installed, in which case fall back to
            # Odoo's own percentage discount.
            discount = self._opt(pl, "ri_src_discount", 0.0) or 0.0
            if not discount and pl.discount:
                discount = qty * (pl.price_unit or 0.0) * (pl.discount / 100.0)
            gross = total + discount
            tax = total - net

            notes = []
            if not product.is_storable:
                cost = 0.0
                notes.append(_("non-stok"))
            else:
                cost = product.with_company(company).standard_price or 0.0
                if not cost:
                    notes.append(_("biaya nol"))
                    zero_cost_qty += qty
            cogs = qty * cost
            margin = net - cogs

            rows.append(
                {
                    "store_code": store_code,
                    "store_name": order.session_id.config_id.name or "",
                    "register": register,
                    "txn_no": txn_no,
                    "txn_date": order.date_order.date() if order.date_order else None,
                    "item_code": product.default_code or "",
                    "item_name": pl.full_product_name or product.display_name or "",
                    "categ": product.categ_id.display_name or "",
                    "qty": qty,
                    "gross": gross,
                    "discount": discount,
                    "tax": tax,
                    "total": total,
                    "net": net,
                    "cogs": cogs,
                    "margin": margin,
                    "note": ", ".join(notes),
                }
            )
            for key in tot:
                tot[key] += rows[-1][key]

        if not rows:
            rows.append({"type": "note", "store_code": _("Tidak ada transaksi POS pada periode tersebut.")})
        rows.append(dict(tot, type="grand_total", store_code=_("TOTAL"), note=""))
        if zero_cost_qty:
            rows.append(
                {
                    "type": "note",
                    "store_code": _(
                        "%(qty).2f unit terjual dengan biaya nol — COGS dan margin baris tersebut "
                        "belum mencerminkan biaya sebenarnya (produk terjual sebelum pernah dibeli).",
                        qty=zero_cost_qty,
                    ),
                }
            )
        return rows
