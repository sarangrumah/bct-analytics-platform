# -*- coding: utf-8 -*-
"""Override BAST generation to auto-populate lines from rental.asset BOM.

The base ``custom_rental.action_generate_bast_pickup/return`` creates an
empty BAST. After super(), if the asset has a usable BOM, we add one
``custom.bast.line`` per exploded component (description, product, qty,
uom, default condition=good).
"""

from __future__ import annotations

from odoo import models


class RentalOrder(models.Model):
    _inherit = "rental.order"

    def _populate_bast_from_bom(self, bast):
        """Fill ``bast.line_ids`` from the rental asset's exploded BOM.

        Called after BAST creation; idempotent — skips if lines already exist.
        """
        self.ensure_one()
        if not bast or bast.line_ids:
            return
        if not self.asset_id:
            return
        components = self.asset_id._explode_components(qty=1.0)
        if not components:
            return
        Line = self.env["custom.bast.line"].sudo()
        seq = 10
        for comp in components:
            product = comp["product"]
            if not product:
                continue
            Line.create(
                {
                    "bast_id": bast.id,
                    "sequence": seq,
                    "item_description": product.display_name,
                    "product_id": product.id,
                    "qty": comp["qty"],
                    "uom_id": (comp["uom"] or product.uom_id).id,
                    "condition": "good",
                }
            )
            seq += 10
        bast.message_post(
            body="BAST lines auto-populated from BOM of asset %s." % self.asset_id.display_name,
        )

    def action_generate_bast_pickup(self):
        res = super().action_generate_bast_pickup()
        for rec in self:
            if rec.bast_pickup_id:
                rec._populate_bast_from_bom(rec.bast_pickup_id)
        return res

    def action_generate_bast_return(self):
        res = super().action_generate_bast_return()
        for rec in self:
            if rec.bast_return_id:
                rec._populate_bast_from_bom(rec.bast_return_id)
        return res
