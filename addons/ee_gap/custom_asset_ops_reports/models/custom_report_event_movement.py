# -*- coding: utf-8 -*-
"""#16 History Record based on Event — in/out movement per rental event.

One row per stock move on a rental order's pickup (OUT) or return (IN) picking,
grouped by event (the ``rental.order``) with a per-event subtotal of quantity.
The ``is_loan`` flag distinguishes loan/cadangan tools from rented units.

Scope: rental orders whose pickup date falls in the report period.
"""

from datetime import datetime, time, timedelta

from odoo import models


class CustomReportEventMovement(models.AbstractModel):
    _name = "custom.report.event.movement"
    _inherit = "custom.report.engine"
    _description = "Event Movement Report"

    _report_code = "event_movement"
    _report_title = "History Aset per Event"

    def _xlsx_columns(self):
        return [
            {"header": "Event", "field": "event", "kind": "text", "width": 20},
            {"header": "Partner", "field": "partner", "kind": "text", "width": 26},
            {"header": "Event Date", "field": "event_date", "kind": "text", "width": 16},
            {"header": "Direction", "field": "direction", "kind": "text", "width": 10},
            {"header": "Product", "field": "product", "kind": "text", "width": 28},
            {"header": "Serial/Lot", "field": "lot", "kind": "text", "width": 22},
            {"header": "Qty", "field": "qty", "kind": "number", "width": 10},
            {"header": "Type", "field": "kind", "kind": "text", "width": 10},
            {"header": "From", "field": "src", "kind": "text", "width": 20},
            {"header": "To", "field": "dest", "kind": "text", "width": 20},
            {"header": "State", "field": "state", "kind": "text", "width": 12},
        ]

    def _move_rows(self, order, picking, direction):
        rows = []
        for move in picking.move_ids:
            lots = ", ".join(m.lot_id.name for m in move.move_line_ids if m.lot_id)
            qty = move.quantity if move.state == "done" else move.product_uom_qty
            rows.append(
                {
                    "event": order.name or "",
                    "partner": order.partner_id.display_name or "",
                    "event_date": order.pickup_dt.strftime("%d-%b-%Y") if order.pickup_dt else "",
                    "direction": direction,
                    "product": move.product_id.display_name or "",
                    "lot": lots,
                    "qty": qty or 0.0,
                    "kind": "Tool/Loan" if move.is_loan else "Rental",
                    "src": move.location_id.display_name or "",
                    "dest": move.location_dest_id.display_name or "",
                    "state": move.state or "",
                }
            )
        return rows

    def _build_lines(self, filters):
        start = datetime.combine(filters["date_from"], time.min)
        end = datetime.combine(filters["date_to"], time.min) + timedelta(days=1)
        domain = [
            ("company_id", "in", filters["company_ids"]),
            ("pickup_dt", ">=", start),
            ("pickup_dt", "<", end),
        ]
        if filters.get("partner_ids"):
            domain.append(("partner_id", "in", filters["partner_ids"]))

        orders = self.env["rental.order"].search(domain, order="pickup_dt, name")
        lines = []
        g_qty = 0.0
        for order in orders:
            event_rows = []
            if order.pickup_picking_id:
                event_rows += self._move_rows(order, order.pickup_picking_id, "OUT")
            if order.return_picking_id:
                event_rows += self._move_rows(order, order.return_picking_id, "IN")
            if not event_rows:
                continue
            s_qty = 0.0
            for row in event_rows:
                lines.append(row)
                s_qty += row["qty"]
                g_qty += row["qty"]
            lines.append({"type": "subtotal", "event": "Subtotal: %s" % (order.name or "—"), "qty": s_qty})

        lines.append({"type": "grand_total", "event": "Grand Total", "qty": g_qty})
        return lines
