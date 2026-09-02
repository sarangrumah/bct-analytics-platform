# -*- coding: utf-8 -*-
"""Portal page where external/internal approvers act on pending requests."""

from __future__ import annotations

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class ApprovalPortal(http.Controller):
    @http.route("/my/approvals", type="http", auth="user", website=True)
    def my_approvals(self):
        user = request.env.user
        pending = (
            request.env["approval.request"]
            .sudo()
            .search(
                [("pending_approver_ids", "in", [user.id]), ("state", "=", "pending")],
                order="due_at asc",
            )
        )
        return request.render("custom_approval_engine.portal_my_approvals", {"pending": pending})

    @http.route("/my/approvals/<int:request_id>", type="http", auth="user", website=True)
    def approval_detail(self, request_id: int):
        req = request.env["approval.request"].sudo().browse(request_id)
        if not req.exists():
            return request.not_found()
        # Visibility: must be pending approver, requester, or have seen a history line
        user = request.env.user
        if user not in req.pending_approver_ids and user != req.requested_by_id:
            raise AccessError("You don't have access to this approval request.")
        return request.render("custom_approval_engine.portal_approval_detail", {"req": req})

    @http.route(
        "/my/approvals/<int:request_id>/decide",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
        website=True,
    )
    def approval_decide(self, request_id: int, action: str = "", comment: str = ""):
        req = request.env["approval.request"].sudo().browse(request_id)
        if not req.exists():
            return request.not_found()
        user = request.env.user
        if user not in req.pending_approver_ids:
            raise AccessError("You are not in the pending approver list.")
        # Run as the actual user so audit attribution is correct
        req_as_user = req.with_user(user)
        if action == "approve":
            req_as_user.action_approve(comment=comment or None)
        elif action == "reject":
            req_as_user.action_reject(comment=comment or None)
        return request.redirect("/my/approvals")
