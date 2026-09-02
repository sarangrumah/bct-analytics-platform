# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    x_custom_po_return_ids = fields.Many2many(
        comodel_name="custom.po.return",
        compute="_compute_x_custom_po_returns",
        string="PO Returns",
    )
    x_custom_po_return_count = fields.Integer(
        compute="_compute_x_custom_po_returns",
    )

    @api.depends("order_line")
    def _compute_x_custom_po_returns(self):
        Allocation = self.env["custom.po.return.allocation"].sudo()
        for order in self:
            returns = Allocation.search([("purchase_line_id", "in", order.order_line.ids)]).return_id
            order.x_custom_po_return_ids = returns
            order.x_custom_po_return_count = len(returns)

    def action_view_po_returns(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("PO Returns"),
            "res_model": "custom.po.return",
            "view_mode": "list,form",
            "domain": [("id", "in", self.x_custom_po_return_ids.ids)],
        }


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    x_custom_returned_qty = fields.Float(
        compute="_compute_x_custom_return_qtys",
        string="Returned Qty",
        digits="Product Unit",
        help="Quantity already returned to the vendor (done return moves).",
    )
    x_custom_returnable_qty = fields.Float(
        compute="_compute_x_custom_return_qtys",
        string="Returnable Qty",
        digits="Product Unit",
        help="Received quantity minus done returns and pending PO Return allocations.",
    )

    def _compute_x_custom_return_qtys(self):
        Allocation = self.env["custom.po.return.allocation"].sudo()
        for line in self:
            returned = 0.0
            for move in line.move_ids:
                if (
                    move.state == "done"
                    and move._is_purchase_return()
                    and (not move.origin_returned_move_id or move.to_refund)
                ):
                    returned += move.product_uom._compute_quantity(move.quantity, line.product_uom_id)
            pending = Allocation.search(
                [
                    ("purchase_line_id", "=", line.id),
                    ("return_id.state", "in", ("draft", "allocated")),
                ]
            )
            pending_qty = (
                line.product_id.uom_id._compute_quantity(sum(pending.mapped("qty")), line.product_uom_id)
                if pending
                else 0.0
            )
            line.x_custom_returned_qty = returned
            # qty_received is already net of done returns (to_refund moves)
            line.x_custom_returnable_qty = line.qty_received - pending_qty
