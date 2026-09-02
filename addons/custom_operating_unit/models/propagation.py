# Part of custom_operating_unit. Licence: LGPL-3.
"""Propagation of the Operating Unit down a document chain.

Stamping ``operating_unit_id`` on four models is not enough on its own: a sales order in
`Cabang Bandung` produces a delivery and a customer invoice, and Odoo's standard
``_prepare_invoice()`` / procurement machinery knows nothing about the field. Without the hooks
below, `sale_order.operating_unit_id` is set while the invoice and the picking it generated are
NULL - and `mart_revenue_daily`, which reads `account_move`, silently loses every row.

That is not a theoretical worry: it is exactly what the demo seeder produced before these hooks
existed (4 sale orders with a unit, 4 posted invoices and 4 pickings with none).

Each hook is defensive about fields that only exist when an optional bridge module is installed
(``sale_stock`` supplies ``sale.order.picking_ids`` and ``stock.picking.sale_id``), so this module
keeps working with any subset of the apps installed.
"""

from odoo import api, models


def _propagate(target, unit):
    """Set ``unit`` on ``target`` when the target has no unit of its own."""
    if unit and target and not target.operating_unit_id:
        target.operating_unit_id = unit


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _prepare_invoice(self):
        values = super()._prepare_invoice()
        if self.operating_unit_id:
            values["operating_unit_id"] = self.operating_unit_id.id
        return values

    def _action_confirm(self):
        result = super()._action_confirm()
        for order in self:
            if not order.operating_unit_id or "picking_ids" not in order._fields:
                continue
            for picking in order.picking_ids:
                _propagate(picking, order.operating_unit_id)
        return result


class StockPicking(models.Model):
    _inherit = "stock.picking"

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        for picking in pickings:
            if picking.operating_unit_id:
                continue
            # Backorders first: a backorder always belongs to the same unit as its parent.
            source = picking.backorder_id
            if not source and "sale_id" in picking._fields:
                source = picking.sale_id
            if source:
                _propagate(picking, source.operating_unit_id)
        return pickings


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _prepare_invoice_vals(self):
        values = super()._prepare_invoice_vals()
        if self.operating_unit_id:
            values["operating_unit_id"] = self.operating_unit_id.id
        return values


# NOTE on credit notes: account.move._reverse_moves() builds the reversal with
# `move.copy(default_values)`, and operating_unit_id is a plain Many2one with copy=True, so the
# credit note inherits the invoice's unit with no hook at all. Contract 03's `revenue_net` nets
# credit notes off invoiced revenue at the same grain, which needs exactly that. Verified against
# odoo/addons/account/models/account_move.py in this image; do not add a hook here.
