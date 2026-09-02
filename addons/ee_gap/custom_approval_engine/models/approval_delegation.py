# -*- coding: utf-8 -*-
"""Manual delegation: user A asks user B to act on their behalf for a window."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ApprovalDelegation(models.Model):
    _name = "approval.delegation"
    _description = "Approval Delegation"
    _order = "valid_from desc"
    _inherit = ["mail.thread"]

    user_id = fields.Many2one("res.users", string="Delegator", required=True, tracking=True)
    delegate_to_id = fields.Many2one("res.users", string="Delegate To", required=True, tracking=True)
    valid_from = fields.Datetime(required=True, tracking=True)
    valid_until = fields.Datetime(required=True, tracking=True)
    reason = fields.Char()

    # Empty = applies to every model. Otherwise restrict to the listed models.
    model_ids = fields.Many2many(
        "ir.model",
        "approval_delegation_model_rel",
        "delegation_id",
        "model_id",
        string="Limit to Models",
        domain="[('transient', '=', False)]",
    )

    active = fields.Boolean(default=True, tracking=True)

    @api.constrains("valid_from", "valid_until")
    def _check_dates(self):
        for rec in self:
            if rec.valid_from >= rec.valid_until:
                raise ValidationError(_("'Valid Until' must be after 'Valid From'."))

    @api.constrains("user_id", "delegate_to_id")
    def _check_distinct_users(self):
        for rec in self:
            if rec.user_id == rec.delegate_to_id:
                raise ValidationError(_("Delegator and delegate must be different users."))

    # ---- Lookup helpers used by approval.request._refresh_pending_approvers ----

    @api.model
    def _find_delegated_to(self, user, model_name: str | None = None) -> "ApprovalDelegation":
        """Return an active delegation where ``user`` is the *delegator*.

        Used to determine if an approver's incoming approvals should be
        redirected to their delegate.
        """
        now = fields.Datetime.now()
        domain = [
            ("user_id", "=", user.id),
            ("active", "=", True),
            ("valid_from", "<=", now),
            ("valid_until", ">=", now),
        ]
        candidates = self.sudo().search(domain, order="valid_from desc")
        for c in candidates:
            if not c.model_ids:
                return c
            if model_name and any(m.model == model_name for m in c.model_ids):
                return c
        return self.browse()

    @api.model
    def _find_delegating(self, user, model_name: str | None = None) -> "ApprovalDelegation":
        """Return an active delegation where ``user`` is the *delegate*.

        Used to attribute an action to the delegator in the audit line.
        """
        now = fields.Datetime.now()
        domain = [
            ("delegate_to_id", "=", user.id),
            ("active", "=", True),
            ("valid_from", "<=", now),
            ("valid_until", ">=", now),
        ]
        candidates = self.sudo().search(domain, order="valid_from desc")
        for c in candidates:
            if not c.model_ids:
                return c
            if model_name and any(m.model == model_name for m in c.model_ids):
                return c
        return self.browse()
