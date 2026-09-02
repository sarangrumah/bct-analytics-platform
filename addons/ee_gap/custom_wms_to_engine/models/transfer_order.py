# -*- coding: utf-8 -*-
"""Transfer order — concrete internal movement proposal/execution."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATE = [
    ("draft", "Draft"),
    ("proposed", "Proposed"),
    ("in_progress", "In Progress"),
    ("done", "Done"),
    ("canceled", "Canceled"),
]


class TransferOrder(models.Model):
    _name = "custom.transfer.order"
    _description = "Transfer Order"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda s: s.env._("New"))
    rule_id = fields.Many2one("custom.to.rule", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(STATE, default="draft", tracking=True, index=True)
    source_location_id = fields.Many2one("stock.location", required=True, index=True)
    target_location_id = fields.Many2one("stock.location", required=True, index=True)
    product_id = fields.Many2one("product.product", required=True, index=True)
    lot_id = fields.Many2one("stock.lot")
    planned_qty = fields.Float(default=0.0, required=True)
    actual_qty = fields.Float(default=0.0)
    picker_id = fields.Many2one("res.users")
    picked_at = fields.Datetime()
    dropped_at = fields.Datetime()
    stock_move_id = fields.Many2one("stock.move", ondelete="set null")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                seq = self.env["ir.sequence"].next_by_code("custom.transfer.order")
                vals["name"] = seq or _("TO/NEW")
        return super().create(vals_list)

    def action_propose(self):
        for rec in self:
            rec.state = "proposed"

    def action_start(self):
        for rec in self:
            if rec.state not in ("draft", "proposed"):
                raise UserError(_("Order %s cannot be started.") % rec.display_name)
            rec.state = "in_progress"
            rec.picker_id = self.env.user.id
            rec.picked_at = fields.Datetime.now()

    def action_done(self):
        for rec in self:
            rec.state = "done"
            rec.dropped_at = fields.Datetime.now()
            rec.actual_qty = rec.actual_qty or rec.planned_qty

    def action_cancel(self):
        for rec in self:
            rec.state = "canceled"

    def action_materialize(self):
        """Create the backing stock.move internal transfer."""
        engine = self.env["custom.to.engine"]
        for rec in self:
            if rec.stock_move_id:
                continue
            move = engine.materialize(
                {
                    "source_location_id": rec.source_location_id.id,
                    "target_location_id": rec.target_location_id.id,
                    "product_id": rec.product_id.id,
                    "lot_id": rec.lot_id.id if rec.lot_id else False,
                    "planned_qty": rec.planned_qty,
                    "name": rec.name,
                    "company_id": rec.company_id.id,
                },
                transfer_order=rec,
            )
            rec.stock_move_id = move.id
