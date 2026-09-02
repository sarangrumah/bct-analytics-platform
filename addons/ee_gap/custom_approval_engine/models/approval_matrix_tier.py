# -*- coding: utf-8 -*-
"""Ordered tiers within an approval matrix."""

from __future__ import annotations

import ast
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ApprovalMatrixTier(models.Model):
    _name = "approval.matrix.tier"
    _description = "Approval Matrix Tier"
    _order = "matrix_id, sequence, id"

    matrix_id = fields.Many2one("approval.matrix", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10, required=True)
    name = fields.Char(required=True, translate=True)

    approver_type = fields.Selection(
        [
            ("user", "Specific users"),
            ("group", "Members of a group"),
            ("manager_of_creator", "Manager of record creator"),
            ("domain", "Domain on res.users"),
        ],
        required=True,
        default="user",
    )
    approver_ids = fields.Many2many(
        "res.users",
        "approval_tier_user_rel",
        "tier_id",
        "user_id",
        string="Approvers",
        domain=[("share", "=", False)],
    )
    approver_group_id = fields.Many2one("res.groups", string="Approver Group")
    approver_domain = fields.Char(
        string="Approver Domain",
        help="Odoo domain on res.users (e.g. [('group_ids','in',[ref('account.group_account_manager')])]).",
    )

    require_all = fields.Boolean(
        string="Require All Approvers",
        default=False,
        help="If checked, every resolved approver must approve. Otherwise any one is enough.",
    )

    sla_hours = fields.Float(
        default=24.0,
        help="Hours until this tier overdues. Cron checks every 15 min.",
    )
    on_overdue = fields.Selection(
        [
            ("auto_approve", "Auto-approve"),
            ("escalate_to_next", "Escalate to next tier"),
            ("escalate_to_user", "Escalate to fallback user"),
            ("none", "No action (just notify)"),
        ],
        default="escalate_to_next",
        required=True,
    )
    escalation_user_id = fields.Many2one(
        "res.users",
        string="Fallback Approver",
        domain=[("share", "=", False)],
        help="Used when ``on_overdue = escalate_to_user``.",
    )

    notify_on_overdue = fields.Boolean(default=True)

    @api.constrains(
        "approver_type", "approver_ids", "approver_group_id", "approver_domain", "on_overdue", "escalation_user_id"
    )
    def _check_config(self):
        for rec in self:
            if rec.approver_type == "user" and not rec.approver_ids:
                raise ValidationError(_("Tier '%s': pick at least one approver.") % rec.name)
            if rec.approver_type == "group" and not rec.approver_group_id:
                raise ValidationError(_("Tier '%s': pick an approver group.") % rec.name)
            if rec.approver_type == "domain":
                try:
                    parsed = ast.literal_eval(rec.approver_domain or "[]")
                    if not isinstance(parsed, list):
                        raise ValueError("Domain must be a list literal")
                except (SyntaxError, ValueError) as e:
                    raise ValidationError(
                        _("Tier '%(name)s': invalid approver domain: %(err)s", name=rec.name, err=e)
                    ) from e
            if rec.on_overdue == "escalate_to_user" and not rec.escalation_user_id:
                raise ValidationError(_("Tier '%s': fallback user required for escalate-to-user.") % rec.name)

    @api.constrains("sla_hours")
    def _check_sla(self):
        for rec in self:
            if rec.sla_hours <= 0:
                raise ValidationError(_("Tier '%s': sla_hours must be > 0.") % rec.name)

    # ---- Approver resolution ----

    def _resolve_approvers(self, record) -> "models.Model":
        """Return ``res.users`` recordset eligible to approve this tier for ``record``."""
        self.ensure_one()
        Users = self.env["res.users"].sudo()
        if self.approver_type == "user":
            return self.approver_ids
        if self.approver_type == "group":
            return self.approver_group_id.user_ids.filtered(lambda u: not u.share)
        if self.approver_type == "manager_of_creator":
            creator = getattr(record, "create_uid", False) or self.env.user
            employee = self.env["hr.employee"].sudo().search([("user_id", "=", creator.id)], limit=1)
            mgr = employee.parent_id.user_id if employee and employee.parent_id else False
            return mgr or Users.browse()
        if self.approver_type == "domain":
            try:
                domain = ast.literal_eval(self.approver_domain or "[]")
            except (SyntaxError, ValueError):
                return Users.browse()
            return Users.search(domain)
        return Users.browse()
