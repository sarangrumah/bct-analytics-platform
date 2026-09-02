# -*- coding: utf-8 -*-
"""Attach approval workflow to any model.

Usage in a downstream model::

    class PurchaseOrder(models.Model):
        _name = "purchase.order"
        _inherit = ["purchase.order", "approval.mixin"]

The mixin adds two stored relational/computed fields, three actions
(submit / cancel / reset), and a single helper ``_approval_check_required``
that downstream actions (post, confirm, ...) MUST call before performing
the protected operation.
"""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ApprovalMixin(models.AbstractModel):
    _name = "approval.mixin"
    _description = "Approval Workflow Mixin"

    x_custom_approval_request_id = fields.Many2one(
        "approval.request",
        string="Approval Request",
        compute="_compute_approval_request",
        store=True,
        copy=False,
    )
    x_custom_approval_state = fields.Selection(
        related="x_custom_approval_request_id.state",
        store=True,
        readonly=True,
    )

    def _compute_approval_request(self):
        """Find the latest non-cancelled approval.request for each record."""
        Req = self.env["approval.request"].sudo()
        for rec in self:
            req = Req.search(
                [
                    ("res_model", "=", rec._name),
                    ("res_id", "=", rec.id),
                    ("state", "!=", "cancelled"),
                ],
                order="create_date desc",
                limit=1,
            )
            rec.x_custom_approval_request_id = req

    # --------------------------------------------------------------
    # Actions (called from form buttons in downstream view inherits)
    # --------------------------------------------------------------

    def action_request_approval(self):
        Req = self.env["approval.request"].sudo()
        for rec in self:
            req = Req._create_for_record(rec)
            if not req:
                raise UserError(_("No active approval matrix matches this %s.") % rec._description)
            if req.state == "draft":
                req.action_submit()
            rec.x_custom_approval_request_id = req.id
        return True

    def action_cancel_approval(self):
        for rec in self:
            if rec.x_custom_approval_request_id:
                rec.x_custom_approval_request_id.action_cancel(reason="Cancelled from record")

    def action_open_approval_request(self):
        self.ensure_one()
        if not self.x_custom_approval_request_id:
            raise UserError(_("No approval request linked yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "approval.request",
            "res_id": self.x_custom_approval_request_id.id,
            "view_mode": "form",
        }

    # --------------------------------------------------------------
    # Gate helper — downstream calls this from button_confirm / _post
    # --------------------------------------------------------------

    def _approval_check_required(self) -> bool:
        """Return True if the protected action may proceed.

        Logic:
          * No matrix matches → no approval needed → True.
          * Matrix matches and request is `approved` → True.
          * Anything else → raise UserError prompting the user to request /
            await approval.
        """
        self.ensure_one()
        matrix = self.env["approval.matrix"].sudo()._resolve_for(self)
        if not matrix:
            return True
        req = self.x_custom_approval_request_id
        if not req:
            raise UserError(
                _("This record requires approval (matrix '%s'). Click 'Request Approval' before continuing.")
                % matrix.name
            )
        if req.state == "approved":
            return True
        if req.state == "rejected":
            raise UserError(_("This record's approval was rejected. Cancel and revise to resubmit."))
        if req.state in ("draft", "pending"):
            raise UserError(
                _(
                    "Approval is %(state)s on tier '%(tier)s'. Wait for completion before continuing.",
                    state=req.state,
                    tier=req.current_tier_id.name or "?",
                )
            )
        if req.state == "cancelled":
            raise UserError(_("Previous approval was cancelled. Start a new approval request."))
        return False

    # --------------------------------------------------------------
    # Auto-submit gate — confirm/post/submit actions call this instead of
    # the raising _approval_check_required. It creates + submits the request
    # when approval is required and returns False so the caller skips the
    # protected operation; the document is left "Waiting Approval".
    # --------------------------------------------------------------

    def _approval_request_or_proceed(self) -> bool:
        """Return True only when the protected action may proceed now.

        Side effect: when a matrix matches and the record is not yet
        approved, auto-create + submit an ``approval.request`` and return
        False (the caller MUST NOT proceed — the record now waits for
        approval). This must never raise on the "needs approval" path, or
        the freshly created request would be rolled back with the
        transaction.
        """
        self.ensure_one()
        matrix = self.env["approval.matrix"].sudo()._resolve_for(self)
        if not matrix:
            return True
        req = self.x_custom_approval_request_id
        if req and req.state == "approved":
            return True
        if req and req.state in ("draft", "pending"):
            # Already waiting — idempotent, do not submit a second request.
            return False
        # No active request (none / rejected / cancelled) → start a fresh cycle.
        new_req = self.env["approval.request"].sudo()._create_for_record(self, matrix=matrix)
        if not new_req:
            return True
        if new_req.state == "draft":
            new_req.action_submit()
        self.x_custom_approval_request_id = new_req.id
        return False

    def _approval_on_granted(self):
        """Hook: re-run the gated action after final approval. Default no-op.

        Overridden per model (sale → action_confirm, purchase →
        button_confirm, account.move → action_post, ...). Called by
        ``approval.request`` once all tiers are approved, running as the
        original requester. Re-entrancy is safe: the gated action calls
        ``_approval_request_or_proceed`` which sees the approved request and
        returns True, so the action proceeds exactly once.
        """
        return True
