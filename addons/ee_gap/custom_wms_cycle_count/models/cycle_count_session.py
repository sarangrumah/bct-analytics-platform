# -*- coding: utf-8 -*-
"""Cycle-count session — the actual run instance produced from a plan."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATE = [
    ("draft", "Draft"),
    ("in_progress", "In Progress"),
    ("reviewing", "Reviewing"),
    ("closed", "Closed"),
    ("canceled", "Canceled"),
]


class CycleCountSession(models.Model):
    _name = "custom.cycle.count.session"
    _description = "Cycle Count Session"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "scheduled_date desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda s: s.env._("New"))
    plan_id = fields.Many2one("custom.cycle.count.plan", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    warehouse_id = fields.Many2one(related="plan_id.warehouse_id", store=True)
    scheduled_date = fields.Date(default=fields.Date.context_today, tracking=True)
    started_at = fields.Datetime()
    completed_at = fields.Datetime()
    assigned_user_ids = fields.Many2many("res.users", string="Assigned Counters")
    state = fields.Selection(STATE, default="draft", tracking=True, index=True)
    line_ids = fields.One2many("custom.cycle.count.line", "session_id")
    line_count = fields.Integer(compute="_compute_counts", store=True)
    variance_count = fields.Integer(compute="_compute_counts", store=True)
    variance_value = fields.Float(compute="_compute_counts", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                seq = self.env["ir.sequence"].next_by_code("custom.cycle.count.session")
                vals["name"] = seq or _("CC/NEW")
        return super().create(vals_list)

    @api.depends(
        "line_ids", "line_ids.variance_qty", "line_ids.expected_qty", "line_ids.counted_qty", "line_ids.product_id"
    )
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            variances = rec.line_ids.filtered(lambda l: l.variance_qty)
            rec.variance_count = len(variances)
            value = 0.0
            for l in variances:
                price = l.product_id.standard_price or 0.0
                value += abs(l.variance_qty or 0.0) * price
            rec.variance_value = value

    def action_start(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Session %s is not in draft.") % rec.display_name)
            rec.state = "in_progress"
            rec.started_at = fields.Datetime.now()

    def action_review(self):
        for rec in self:
            rec.state = "reviewing"

    def action_close(self):
        for rec in self:
            if rec.line_ids.filtered(lambda l: l.status not in ("approved", "skipped")):
                raise UserError(
                    _("All lines must be approved or skipped before closing session %s.") % rec.display_name
                )
            rec.state = "closed"
            rec.completed_at = fields.Datetime.now()

    def action_cancel(self):
        for rec in self:
            rec.state = "canceled"
