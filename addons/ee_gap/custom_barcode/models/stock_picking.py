# -*- coding: utf-8 -*-
"""Small extension of stock.picking used by the QWeb barcode summary report."""

from collections import defaultdict

from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _barcode_summary_sessions(self):
        self.ensure_one()
        return self.env["custom.barcode.scan.session"].search([("picking_id", "=", self.id)])

    def _barcode_summary_rows(self):
        """Return list of dicts: {product, expected, scanned, deviation_pct}."""
        self.ensure_one()
        expected = defaultdict(float)
        for move in self.move_ids:
            expected[move.product_id.id] += move.product_uom_qty

        scanned = defaultdict(float)
        sessions = self._barcode_summary_sessions()
        for line in sessions.mapped("line_ids").filtered(lambda l: l.status == "ok" and l.product_id):
            scanned[line.product_id.id] += line.quantity

        product_ids = set(expected.keys()) | set(scanned.keys())
        Product = self.env["product.product"].browse(list(product_ids))
        rows = []
        for product in Product:
            exp = expected.get(product.id, 0.0)
            sc = scanned.get(product.id, 0.0)
            if exp:
                pct = (sc - exp) / exp * 100.0
            elif sc:
                pct = 100.0
            else:
                pct = 0.0
            rows.append(
                {
                    "product": product,
                    "expected": exp,
                    "scanned": sc,
                    "deviation_pct": pct,
                }
            )
        rows.sort(key=lambda r: r["product"].display_name or "")
        return rows
