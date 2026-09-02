# -*- coding: utf-8 -*-
"""Out-of-Office records — auto-created from approved ``hr.leave`` entries.

A separate model (rather than a flag on res.users) so we can:
  * keep an audit history of OOO windows
  * support multiple overlapping or sequential leaves
  * keep manual delegations (``approval.delegation``) cleanly separated
    from auto-generated ones
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ApprovalOOO(models.Model):
    _name = "approval.ooo"
    _description = "Out-of-Office Auto-Delegation"
    _order = "date_from desc"

    user_id = fields.Many2one("res.users", required=True, index=True)
    leave_id = fields.Many2one(
        "hr.leave",
        ondelete="set null",
        help="Source leave when this OOO was auto-created. Empty for manual OOO.",
    )
    date_from = fields.Datetime(required=True)
    date_to = fields.Datetime(required=True)
    auto_delegate_to_id = fields.Many2one(
        "res.users",
        string="Auto-delegate To",
        help="Approver who receives pending approvals while ``user_id`` is OOO.",
    )
    note = fields.Char()

    active = fields.Boolean(default=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from >= rec.date_to:
                raise ValidationError(_("OOO 'date_to' must be after 'date_from'."))

    # ---- Lookups ----

    @api.model
    def _active_for(self, user) -> "ApprovalOOO":
        now = fields.Datetime.now()
        return self.sudo().search(
            [
                ("user_id", "=", user.id),
                ("active", "=", True),
                ("date_from", "<=", now),
                ("date_to", ">=", now),
            ],
            order="date_from desc",
            limit=1,
        )
