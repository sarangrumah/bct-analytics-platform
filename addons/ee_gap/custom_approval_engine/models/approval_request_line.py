# -*- coding: utf-8 -*-
"""Immutable audit trail line per action on an approval request."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class ApprovalRequestLine(models.Model):
    _name = "approval.request.line"
    _description = "Approval Request History Line"
    _order = "create_date desc, id desc"

    request_id = fields.Many2one("approval.request", required=True, ondelete="cascade", index=True)
    tier_id = fields.Many2one("approval.matrix.tier")
    tier_name = fields.Char(related="tier_id.name", store=True)
    action_user_id = fields.Many2one("res.users", required=True)
    action = fields.Selection(
        [
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("delegated", "Delegated"),
            ("escalated", "Escalated"),
            ("cancelled", "Cancelled"),
            ("commented", "Commented"),
        ],
        required=True,
    )
    action_at = fields.Datetime(default=fields.Datetime.now, required=True)
    comment = fields.Text()
    delegated_from_id = fields.Many2one(
        "res.users",
        string="On Behalf Of",
        help="Set when the action was taken via an active delegation.",
    )

    # Lines are append-only — protect against tampering.
    def write(self, vals):
        if self.env.context.get("approval_line_internal_write"):
            return super().write(vals)
        raise UserError(_("Approval history lines are immutable."))

    def unlink(self):
        raise UserError(_("Approval history lines are immutable."))
