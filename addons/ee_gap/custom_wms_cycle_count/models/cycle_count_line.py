# -*- coding: utf-8 -*-
"""Cycle-count line — one (location, product[, lot]) tuple to count."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATUS = [
    ("pending", "Pending"),
    ("counted", "Counted"),
    ("skipped", "Skipped"),
    ("recount_required", "Recount Required"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
]


class CycleCountLine(models.Model):
    _name = "custom.cycle.count.line"
    _description = "Cycle Count Line"
    _inherit = ["pdp.audited.mixin"]
    _order = "session_id, sequence, id"

    session_id = fields.Many2one(
        "custom.cycle.count.session",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    location_id = fields.Many2one("stock.location", required=True, index=True)
    product_id = fields.Many2one("product.product", index=True)
    lot_id = fields.Many2one("stock.lot")
    expected_qty = fields.Float(default=0.0)
    counted_qty = fields.Float(default=0.0)
    variance_qty = fields.Float(compute="_compute_variance", store=True)
    variance_pct = fields.Float(compute="_compute_variance", store=True)
    status = fields.Selection(STATUS, default="pending", index=True)
    counter_user_id = fields.Many2one("res.users")
    counted_at = fields.Datetime()
    remark = fields.Char()
    is_new_item = fields.Boolean(default=False)
    new_item_product_temp_name = fields.Char(string="Unrecognised Item Name")

    @api.depends("expected_qty", "counted_qty")
    def _compute_variance(self):
        for rec in self:
            rec.variance_qty = (rec.counted_qty or 0.0) - (rec.expected_qty or 0.0)
            base = rec.expected_qty or 0.0
            # Zero-division guard: when expected is 0 we return 0 (test target).
            rec.variance_pct = 0.0 if not base else 100.0 * rec.variance_qty / base

    def action_count(self, qty):
        self.ensure_one()
        self.counted_qty = qty
        self.counted_at = fields.Datetime.now()
        self.counter_user_id = self.env.user.id
        self.status = "counted"

    def action_approve(self):
        if not self.env.user.has_group("custom_wms_cycle_count.group_cycle_count_supervisor"):
            raise UserError(_("Only supervisors may approve cycle count lines."))
        for rec in self:
            rec.status = "approved"
            if rec.variance_qty:
                self.env["custom.cycle.count.adjustment"].create(
                    {
                        "line_id": rec.id,
                        "approved_by_id": self.env.user.id,
                        "approved_at": fields.Datetime.now(),
                    }
                )

    def action_reject(self):
        if not self.env.user.has_group("custom_wms_cycle_count.group_cycle_count_supervisor"):
            raise UserError(_("Only supervisors may reject cycle count lines."))
        for rec in self:
            rec.status = "rejected"

    def action_recount(self):
        for rec in self:
            rec.status = "recount_required"
