# -*- coding: utf-8 -*-
"""Cycle-count adjustment — the variance-posting record."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class CycleCountAdjustment(models.Model):
    _name = "custom.cycle.count.adjustment"
    _description = "Cycle Count Adjustment"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc, id desc"

    line_id = fields.Many2one("custom.cycle.count.line", required=True, ondelete="cascade", index=True)
    stock_move_id = fields.Many2one("stock.move", string="Stock Move", ondelete="set null")
    approved_by_id = fields.Many2one("res.users")
    approved_at = fields.Datetime()
    posted = fields.Boolean(default=False, tracking=True)

    def action_post(self):
        for rec in self:
            if rec.posted:
                continue
            line = rec.line_id
            if not line or not line.location_id or not line.product_id:
                raise UserError(_("Cannot post adjustment without product + location."))
            # Create a stock.move documenting the variance against the inventory loss location.
            warehouse = line.session_id.warehouse_id
            inv_loc = self.env.ref("stock.location_inventory", raise_if_not_found=False)
            if not inv_loc:
                inv_loc = self.env["stock.location"].search([("usage", "=", "inventory")], limit=1)
            if not inv_loc:
                raise UserError(_("Inventory adjustment location not found."))
            qty = line.variance_qty or 0.0
            if qty == 0.0:
                rec.posted = True
                continue
            src, dst = (line.location_id, inv_loc) if qty < 0 else (inv_loc, line.location_id)
            company = warehouse.company_id or self.env.company
            label = _("Cycle count adjustment %s") % line.session_id.name
            # Built to match how core books an inventory adjustment
            # (stock.quant._get_inventory_move_values): is_inventory + a ready
            # move line + picked, so _action_done actually moves the stock.
            #
            # Two Odoo 19 traps here:
            #   * stock.move.name was removed — passing it raises
            #     ValueError: Invalid field 'name' in 'stock.move'.
            #   * reference is compute+store with NO inverse, so writing it is
            #     silently discarded. The label reaches it through
            #     inventory_name, which _compute_reference reads for inventory
            #     moves.
            move = self.env["stock.move"].create(
                {
                    "is_inventory": True,
                    "inventory_name": label,
                    "product_id": line.product_id.id,
                    "product_uom": line.product_id.uom_id.id,
                    "product_uom_qty": abs(qty),
                    "location_id": src.id,
                    "location_dest_id": dst.id,
                    "company_id": company.id,
                    "state": "confirmed",
                    "picked": True,
                    "move_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": line.product_id.id,
                                "product_uom_id": line.product_id.uom_id.id,
                                "quantity": abs(qty),
                                "location_id": src.id,
                                "location_dest_id": dst.id,
                                "company_id": company.id,
                                "lot_id": line.lot_id.id if line.lot_id else False,
                            },
                        )
                    ],
                }
            )
            # Without this the move stays in draft: `posted` would read True
            # while the variance was never reconciled — a silent no-op is worse
            # than the crash this replaced.
            move._action_done()
            rec.stock_move_id = move.id
            rec.posted = True
