# -*- coding: utf-8 -*-
from odoo import api, fields, models


class FSMWorkOrderMaterial(models.Model):
    _name = "fsm.work.order.material"
    _description = "FSM Work Order Material"
    _order = "work_order_id, id"

    work_order_id = fields.Many2one("fsm.work.order", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    quantity = fields.Float(required=True, default=1.0)
    uom_id = fields.Many2one(
        "uom.uom",
        compute="_compute_uom",
        store=True,
        readonly=False,
    )
    note = fields.Char()
    unit_cost = fields.Monetary(currency_field="currency_id")
    subtotal = fields.Monetary(compute="_compute_subtotal", store=True, currency_field="currency_id")
    currency_id = fields.Many2one(related="work_order_id.company_id.currency_id", store=True)

    @api.depends("product_id")
    def _compute_uom(self):
        for rec in self:
            rec.uom_id = rec.product_id.uom_id

    @api.depends("quantity", "unit_cost")
    def _compute_subtotal(self):
        for rec in self:
            rec.subtotal = (rec.quantity or 0.0) * (rec.unit_cost or 0.0)
