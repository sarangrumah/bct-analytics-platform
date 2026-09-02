# -*- coding: utf-8 -*-
"""Scrap Note data helpers.

``stock.scrap`` is one product per record in Odoo 19, so the printable Scrap
Note groups several scrap orders under one sheet when they share an origin —
that is what a warehouse actually signs off: "these 6 write-offs, one note".
The grouping key is the origin document when present, else the scrap order
itself.
"""

from __future__ import annotations

from odoo import models


class StockScrap(models.Model):
    _name = "stock.scrap"
    # ``wms.barcode.mixin`` gives the Scrap Note its header + line barcodes.
    _inherit = ["stock.scrap", "wms.barcode.mixin"]

    def _wms_scrap_rows(self) -> list[dict]:
        """One dict per scrapped line, ordered by source bin then SKU."""
        rows: list[dict] = []
        ordered = self.sorted(
            key=lambda s: (
                (s.location_id.complete_name or "").upper(),
                (s.product_id.default_code or ""),
                s.id,
            )
        )
        for seq, scrap in enumerate(ordered, start=1):
            item_value = self._wms_item_barcode_value(scrap.product_id, scrap.lot_id)
            cost = scrap.product_id.standard_price or 0.0
            rows.append(
                {
                    "seq": seq,
                    "scrap": scrap,
                    "name": scrap.name or "",
                    "location_name": scrap.location_id.complete_name or "",
                    "scrap_location_name": scrap.scrap_location_id.complete_name or "",
                    "default_code": scrap.product_id.default_code or "",
                    "product": scrap.product_id,
                    "lot_name": scrap.lot_id.name or "",
                    "item_barcode_value": item_value,
                    "item_barcode": self._wms_item_barcode_src(item_value),
                    "qty": scrap.scrap_qty or 0.0,
                    "uom_name": scrap.product_uom_id.display_name or "",
                    "unit_cost": cost,
                    "value": cost * (scrap.scrap_qty or 0.0),
                    "replenish": scrap.should_replenish,
                    "state": scrap.state,
                }
            )
        return rows

    def _wms_scrap_totals(self) -> dict:
        rows = self._wms_scrap_rows()
        return {
            "line_count": len(rows),
            "total_qty": sum(r["qty"] for r in rows),
            "total_value": sum(r["value"] for r in rows),
        }

    def _wms_scrap_header(self) -> dict:
        """Sheet-level header: the note reference and what it covers."""
        first = self[:1]
        origins = sorted({s.origin for s in self if s.origin})
        pickings = self.mapped("picking_id")
        reference = origins[0] if len(origins) == 1 else (first.name or "")
        return {
            "reference": reference,
            "reference_barcode": self._wms_barcode_src(reference, "Code128", 500, 90),
            "origin": ", ".join(origins) or "-",
            "picking_names": ", ".join(pickings.mapped("name")) or "-",
            "company": first.company_id,
            "date": first.date_done or first.create_date,
            "warehouse": first.location_id.warehouse_id.display_name or "-",
        }
