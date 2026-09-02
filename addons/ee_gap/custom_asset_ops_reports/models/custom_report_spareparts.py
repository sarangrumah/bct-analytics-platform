# -*- coding: utf-8 -*-
"""#17 List Sparepart — availability + usage tracking.

One row per spare-part product (the catalog = every product ever referenced by a
maintenance request's ``x_spare_part_ids``), showing current stock availability
(from ``stock.quant`` over internal locations) and how many maintenance requests
consumed it within the report period.

Note: ``x_spare_part_ids`` is a many2many without a per-part quantity, so "Used"
is the count of maintenance requests in the period that included the part, not a
consumed quantity. ``x_parts_cost`` is a per-request total (not per-part) and is
therefore not attributed here.
"""

from odoo import models


class CustomReportSpareParts(models.AbstractModel):
    _name = "custom.report.spareparts"
    _inherit = "custom.report.engine"
    _description = "Spare Parts Report"

    _report_code = "spareparts"
    _report_title = "List Sparepart"

    def _xlsx_columns(self):
        return [
            {"header": "Code", "field": "code", "kind": "text", "width": 16},
            {"header": "Part", "field": "name", "kind": "text", "width": 34},
            {"header": "UoM", "field": "uom", "kind": "text", "width": 10},
            {"header": "On-hand", "field": "on_hand", "kind": "number", "width": 12},
            {"header": "Reserved", "field": "reserved", "kind": "number", "width": 12},
            {"header": "Available", "field": "available", "kind": "number", "width": 12},
            {"header": "Used (period)", "field": "used", "kind": "number", "width": 14},
        ]

    def _stock_by_product(self, product_ids):
        """{product_id: (on_hand, reserved)} over internal locations."""
        out = {}
        if not product_ids:
            return out
        groups = self.env["stock.quant"]._read_group(
            [
                ("product_id", "in", product_ids),
                ("location_id.usage", "=", "internal"),
            ],
            groupby=["product_id"],
            aggregates=["quantity:sum", "reserved_quantity:sum"],
        )
        for product, qty, reserved in groups:
            out[product.id] = (qty or 0.0, reserved or 0.0)
        return out

    def _build_lines(self, filters):
        company_ids = filters["company_ids"]

        # Catalog: every product ever used as a spare part.
        catalog = self.env["maintenance.request"].search([]).mapped("x_spare_part_ids")

        # Usage in period: count requests per part.
        used = {}
        req_domain = [
            ("request_date", ">=", filters["date_from"]),
            ("request_date", "<=", filters["date_to"]),
        ]
        if company_ids:
            req_domain.append(("company_id", "in", company_ids + [False]))
        for req in self.env["maintenance.request"].search(req_domain):
            for part in req.x_spare_part_ids:
                used[part.id] = used.get(part.id, 0) + 1

        stock = self._stock_by_product(catalog.ids)

        lines = []
        for product in catalog.sorted(lambda p: p.default_code or p.name or ""):
            on_hand, reserved = stock.get(product.id, (0.0, 0.0))
            lines.append(
                {
                    "code": product.default_code or "",
                    "name": product.display_name or "",
                    "uom": product.uom_id.name or "",
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "available": on_hand - reserved,
                    "used": used.get(product.id, 0),
                }
            )

        lines.append(
            {
                "type": "grand_total",
                "name": "Total spare parts: %d" % len(catalog),
                "on_hand": sum(r["on_hand"] for r in lines),
                "reserved": sum(r["reserved"] for r in lines),
                "available": sum(r["available"] for r in lines),
                "used": sum(r["used"] for r in lines),
            }
        )
        return lines
