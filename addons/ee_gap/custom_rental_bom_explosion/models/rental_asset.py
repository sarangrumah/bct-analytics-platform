# -*- coding: utf-8 -*-
"""Add explicit BOM link on rental.asset.

If empty, the model falls back to ``product_id.bom_ids`` (first
matching phantom/kit BOM) at explosion time.
"""

from __future__ import annotations

from odoo import api, fields, models


class RentalAsset(models.Model):
    _inherit = "rental.asset"

    bom_id = fields.Many2one(
        "mrp.bom",
        string="Bundle BOM",
        domain="[('product_tmpl_id.is_rentable', '=', True)]",
        help="Optional. If set, this BOM's components are exploded into BAST "
        "lines on pickup/return. If empty, the first phantom/kit BOM linked "
        "to the asset's product is used.",
    )
    has_bundle = fields.Boolean(
        compute="_compute_has_bundle",
        store=False,
        help="True if a usable BOM (explicit or via product) exists.",
    )

    @api.depends("bom_id", "product_id")
    def _compute_has_bundle(self):
        for rec in self:
            rec.has_bundle = bool(rec._resolve_bom())

    def _resolve_bom(self):
        """Return the BOM to explode, or empty recordset.

        Priority:
          1. ``bom_id`` explicitly set on the asset
          2. First ``phantom`` BOM on the product
          3. First BOM on the product (any type)
        """
        self.ensure_one()
        if self.bom_id:
            return self.bom_id
        if not self.product_id:
            return self.env["mrp.bom"]
        Bom = self.env["mrp.bom"].sudo()
        phantom = Bom.search(
            [
                ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
                ("type", "=", "phantom"),
            ],
            limit=1,
        )
        if phantom:
            return phantom
        return Bom.search(
            [("product_tmpl_id", "=", self.product_id.product_tmpl_id.id)],
            limit=1,
        )

    def _explode_components(self, qty=1.0):
        """Return list of dicts: [{product, qty, uom}, ...].

        Uses ``mrp.bom.explode`` when available (returns flattened
        components including sub-BOMs). Falls back to direct bom_line_ids
        iteration if explode fails or BOM is missing.
        """
        self.ensure_one()
        bom = self._resolve_bom()
        if not bom:
            return []
        results = []
        try:
            _boms, lines = bom.explode(self.product_id, qty)
            for line, line_data in lines:
                product = line.product_id
                line_qty = line_data.get("qty", line.product_qty)
                results.append(
                    {
                        "product": product,
                        "qty": line_qty,
                        "uom": line.product_uom_id or product.uom_id,
                    }
                )
        except Exception:
            # Fallback: direct line iteration
            for line in bom.bom_line_ids:
                results.append(
                    {
                        "product": line.product_id,
                        "qty": line.product_qty * qty,
                        "uom": line.product_uom_id or line.product_id.uom_id,
                    }
                )
        return results
