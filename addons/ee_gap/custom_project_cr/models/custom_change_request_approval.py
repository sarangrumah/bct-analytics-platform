# -*- coding: utf-8 -*-
"""One approval tier. Kept as its own record so the decision trail survives."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomChangeRequestApproval(models.Model):
    _name = "custom.change.request.approval"
    _description = "VAS Change Request Approval"
    _inherit = ["pdp.audited.mixin"]
    _order = "request_id, tier"

    request_id = fields.Many2one(
        "custom.change.request",
        required=True,
        ondelete="cascade",
        index=True,
    )
    tier = fields.Integer(required=True)
    role = fields.Selection(
        [
            ("ba", "Business Analyst"),
            ("po", "Product Owner"),
            ("vertical_owner", "Vertical Owner"),
        ],
        required=True,
    )
    user_id = fields.Many2one("res.users", string="Approver", required=True)
    state = fields.Selection(
        [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
        default="pending",
        required=True,
    )
    decided_at = fields.Datetime(readonly=True)
    note = fields.Char()

    _tier_uniq = models.Constraint(
        "unique(request_id, tier)",
        "That approval tier already exists on this request.",
    )

    def action_approve(self):
        for line in self:
            if line.state != "pending":
                raise UserError(_("This tier has already been decided."))
            earlier = line.request_id.approval_ids.filtered(lambda a: a.tier < line.tier and a.state != "approved")
            if earlier:
                raise UserError(
                    _(
                        "Tier %(tier)s cannot approve before tier %(earlier)s has.",
                        tier=line.tier,
                        earlier=min(earlier.mapped("tier")),
                    )
                )
            line.write({"state": "approved", "decided_at": fields.Datetime.now()})
            line._pdp_audit_write(
                "cr_tier_approve",
                line.id,
                {"tier": line.tier, "user_id": line.user_id.id},
            )
            request = line.request_id
            if all(a.state == "approved" for a in request.approval_ids):
                request._on_fully_approved()
        return True

    @api.depends("request_id", "tier", "role")
    def _compute_display_name(self):
        for line in self:
            line.display_name = f"T{line.tier} · {dict(self._fields['role'].selection).get(line.role, line.role)}"
