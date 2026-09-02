# -*- coding: utf-8 -*-
"""Putaway suggestion / proposal — engine output awaiting operator decision."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATUS = [
    ("pending", "Pending"),
    ("accepted", "Accepted"),
    ("overridden", "Overridden"),
    ("applied", "Applied"),
    ("rejected", "Rejected"),
]


class WmsPutawaySuggestion(models.Model):
    _name = "custom.wms.putaway.suggestion"
    _description = "WMS Putaway Suggestion"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc, id desc"
    _check_company_auto = True

    # env._ rather than _: a field default runs with no request frame for
    # translate.py to sniff a language from, which logs a warning with a full
    # stack dump on every create. env._ takes the language from the env.
    name = fields.Char(default=lambda s: s.env._("Suggestion"), tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    picking_id = fields.Many2one("stock.picking", index=True, ondelete="cascade")
    move_line_id = fields.Many2one(
        "stock.move.line",
        required=True,
        ondelete="cascade",
        index=True,
    )
    original_dest_location_id = fields.Many2one("stock.location", string="Original Destination")
    suggested_location_id = fields.Many2one("stock.location", required=True)
    overridden_location_id = fields.Many2one("stock.location", string="Operator Override")
    rule_id = fields.Many2one("custom.wms.putaway.rule", ondelete="set null")
    strategy_id = fields.Many2one(related="rule_id.strategy_id", store=True, index=True)
    score = fields.Integer(default=0, tracking=True)
    confidence_score = fields.Integer(compute="_compute_confidence", store=True, help="Alias for score (0..100).")
    reason = fields.Char()
    status = fields.Selection(STATUS, default="pending", tracking=True, index=True)
    applied_at = fields.Datetime()
    created_at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.depends("score")
    def _compute_confidence(self):
        for rec in self:
            rec.confidence_score = max(0, min(100, int(rec.score or 0)))

    # -- actions ----------------------------------------------------------

    def action_apply(self):
        for rec in self:
            if rec.status == "applied":
                continue
            target = rec.overridden_location_id or rec.suggested_location_id
            if not target:
                raise UserError(_("Suggestion %s has no target location.") % rec.display_name)
            if rec.move_line_id and rec.move_line_id.exists():
                rec.move_line_id.location_dest_id = target.id
            rec.status = "overridden" if rec.overridden_location_id else "applied"
            rec.applied_at = fields.Datetime.now()
        return True

    def action_reject(self):
        for rec in self:
            rec.status = "rejected"
        return True

    def action_accept(self):
        for rec in self:
            if rec.status == "pending":
                rec.status = "accepted"
        return True
