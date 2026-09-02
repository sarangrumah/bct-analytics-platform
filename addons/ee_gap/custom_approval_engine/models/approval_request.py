# -*- coding: utf-8 -*-
"""Approval request — one per (record × matrix) instance."""

from __future__ import annotations

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STATES = [
    ("draft", "Draft"),
    ("pending", "Waiting Approval"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("cancelled", "Cancelled"),
]


class ApprovalRequest(models.Model):
    _name = "approval.request"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _description = "Approval Request"
    _order = "create_date desc"
    _rec_name = "display_name"

    display_name = fields.Char(compute="_compute_display_name", store=True)

    matrix_id = fields.Many2one("approval.matrix", required=True, ondelete="restrict")
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    res_ref = fields.Reference(
        selection="_selection_target_model",
        compute="_compute_res_ref",
        store=False,
    )
    res_name = fields.Char(string="Record Name", compute="_compute_res_name", store=True)

    requested_by_id = fields.Many2one("res.users", default=lambda self: self.env.user, required=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    state = fields.Selection(STATES, default="draft", required=True, tracking=True, index=True)
    current_tier_id = fields.Many2one("approval.matrix.tier", string="Current Tier")
    current_tier_sequence = fields.Integer(related="current_tier_id.sequence", store=True)
    due_at = fields.Datetime(tracking=True)
    overdue = fields.Boolean(compute="_compute_overdue", search="_search_overdue")

    history_ids = fields.One2many("approval.request.line", "request_id", string="History")
    pending_approver_ids = fields.Many2many(
        "res.users",
        "approval_request_pending_user_rel",
        "request_id",
        "user_id",
        string="Pending Approvers",
        help="Effective list at the current tier (after OOO + delegation resolution).",
    )
    final_decision_user_id = fields.Many2one("res.users", string="Decided By", readonly=True)
    decided_at = fields.Datetime(readonly=True)

    # ---------------------------------------------------------------- helpers

    @api.model
    def _selection_target_model(self):
        return [(m.model, m.name) for m in self.env["ir.model"].sudo().search([])]

    @api.depends("res_model", "res_id")
    def _compute_res_ref(self):
        for rec in self:
            rec.res_ref = f"{rec.res_model},{rec.res_id}" if rec.res_model and rec.res_id else False

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        for rec in self:
            if rec.res_model and rec.res_id and rec.res_model in self.env:
                target = self.env[rec.res_model].sudo().browse(rec.res_id)
                rec.res_name = target.display_name if target.exists() else False
            else:
                rec.res_name = False

    @api.depends("res_name", "matrix_id", "state")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.matrix_id.name}: {rec.res_name or rec.res_id} [{rec.state}]"

    def _compute_overdue(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.overdue = bool(rec.state == "pending" and rec.due_at and rec.due_at < now)

    def _search_overdue(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            return [("id", "in", [])]
        now = fields.Datetime.now()
        domain = [("state", "=", "pending"), ("due_at", "<", now)]
        if (operator == "=" and value) or (operator == "!=" and not value):
            return domain
        return ["!", *domain]

    def _pdp_audit_classification(self):  # noqa: D401  (override pdp.audited.mixin)
        return "internal"

    # ---------------------------------------------------------------- creation

    @api.model
    def _create_for_record(self, record, matrix=None):
        """Build a draft approval.request for ``record``. Returns the request or False."""
        matrix = matrix or self.env["approval.matrix"]._resolve_for(record)
        if not matrix:
            return self.browse()
        existing = self.sudo().search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("state", "in", ("draft", "pending")),
            ],
            limit=1,
        )
        if existing:
            return existing
        req = self.sudo().create(
            {
                "matrix_id": matrix.id,
                "res_model": record._name,
                "res_id": record.id,
                # Capture the real acting user (record's env), not the superuser
                # introduced by the sudo() create — used for audit attribution
                # and to run the post-approval auto-proceed with proper ACLs.
                "requested_by_id": record.env.user.id,
                "company_id": (
                    record.company_id.id if hasattr(record, "company_id") and record.company_id else self.env.company.id
                ),
            }
        )
        return req

    # ---------------------------------------------------------------- lifecycle

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft requests can be submitted."))
            first_tier = rec.matrix_id.tier_ids.sorted("sequence")[:1]
            if not first_tier:
                raise UserError(_("Matrix '%s' has no tiers.") % rec.matrix_id.name)
            rec.write(
                {
                    "state": "pending",
                    "current_tier_id": first_tier.id,
                    "due_at": fields.Datetime.now() + timedelta(hours=first_tier.sla_hours),
                }
            )
            rec._refresh_pending_approvers()
            rec._notify_pending()
            rec._pdp_audit_write("approval_submit", rec.id, {"matrix": rec.matrix_id.name, "tier": first_tier.name})
        return True

    def action_approve(self, comment: str | None = None):
        for rec in self:
            if rec.state != "pending":
                raise UserError(_("Only pending requests can be approved."))
            effective_user = rec._effective_actor()
            # Approval-manager users may lack res.users read ACL on other
            # approvers; use sudo for the read-only membership probes.
            sudo_rec = rec.sudo()
            if effective_user not in sudo_rec.pending_approver_ids:
                raise UserError(_("You are not in the pending approver list for the current tier."))
            tier = rec.current_tier_id
            rec._record_line(tier, effective_user, "approved", comment)

            # When require_all, check whether all approvers have approved at this tier
            if tier.require_all:
                approved_users = sudo_rec.history_ids.filtered(
                    lambda l: l.tier_id == tier and l.action == "approved"
                ).mapped("action_user_id")
                if not all(u in approved_users for u in sudo_rec.pending_approver_ids):
                    # Still waiting on others
                    continue
            rec._advance_to_next_tier()
        return True

    def action_reject(self, comment: str | None = None):
        for rec in self:
            if rec.state != "pending":
                raise UserError(_("Only pending requests can be rejected."))
            effective_user = rec._effective_actor()
            if effective_user not in rec.pending_approver_ids:
                raise UserError(_("You are not in the pending approver list for the current tier."))
            rec._record_line(rec.current_tier_id, effective_user, "rejected", comment)
            rec.write(
                {
                    "state": "rejected",
                    "final_decision_user_id": effective_user.id,
                    "decided_at": fields.Datetime.now(),
                }
            )
            try:
                rec.sudo().message_post(body=_("Request rejected by %s") % effective_user.name)
            except Exception:
                _logger.exception("approval %s: message_post failed (non-fatal)", rec.id)
            rec._pdp_audit_write("approval_reject", rec.id, {"comment": comment or ""})
        return True

    def action_cancel(self, reason: str | None = None):
        for rec in self:
            if rec.state not in ("draft", "pending"):
                raise UserError(_("Only draft/pending requests can be cancelled."))
            rec._record_line(rec.current_tier_id, self.env.user, "cancelled", reason)
            rec.write({"state": "cancelled", "decided_at": fields.Datetime.now()})
            rec._pdp_audit_write("approval_cancel", rec.id, {"reason": reason or ""})
        return True

    # ---------------------------------------------------------------- internals

    def _advance_to_next_tier(self):
        self.ensure_one()
        tiers = self.matrix_id.tier_ids.sorted("sequence")
        idx = list(tiers).index(self.current_tier_id) if self.current_tier_id in tiers else -1
        if idx + 1 >= len(tiers):
            # Final tier approved → request approved
            self.write(
                {
                    "state": "approved",
                    "final_decision_user_id": self.env.user.id,
                    "decided_at": fields.Datetime.now(),
                }
            )
            # mail.message creation may hit ACLs for the acting user; the
            # decision itself is already persisted, so swallow notify errors.
            try:
                self.sudo().message_post(body=_("All tiers approved."))
            except Exception:
                _logger.exception("approval %s: message_post failed (non-fatal)", self.id)
            self._pdp_audit_write("approval_complete", self.id, None)
            # Auto-proceed the gated action (confirm / post / submit) now that
            # approval is complete, running as the original requester. Best
            # effort: a failure here (e.g. missing order lines) must not roll
            # back the already-persisted approval decision.
            try:
                record = self._record()
                if record and record.exists() and hasattr(record, "_approval_on_granted"):
                    record.with_user(self.requested_by_id)._approval_on_granted()
            except Exception:
                _logger.exception("approval %s: auto-proceed on grant failed (non-fatal)", self.id)
            return
        next_tier = tiers[idx + 1]
        self.write(
            {
                "current_tier_id": next_tier.id,
                "due_at": fields.Datetime.now() + timedelta(hours=next_tier.sla_hours),
            }
        )
        self._refresh_pending_approvers()
        try:
            self._notify_pending()
        except Exception:
            _logger.exception("approval %s: notify_pending failed (non-fatal)", self.id)
        self._pdp_audit_write("approval_advance", self.id, {"to_tier": next_tier.name})

    def _record_line(self, tier, user, action: str, comment: str | None):
        self.env["approval.request.line"].sudo().create(
            {
                "request_id": self.id,
                "tier_id": tier.id if tier else False,
                "action_user_id": user.id,
                "action": action,
                "comment": comment,
                "delegated_from_id": self._delegator_for(user).id if self._delegator_for(user) else False,
            }
        )

    def _effective_actor(self):
        """Return the user performing the action — current user (delegation handled separately)."""
        return self.env.user

    def _delegator_for(self, user):
        """If ``user`` is acting on behalf of someone via active delegation, return that delegator."""
        delegation = self.env["approval.delegation"].sudo()._find_delegating(user=user, model_name=self.res_model)
        return delegation.user_id if delegation else self.env["res.users"]

    def _refresh_pending_approvers(self):
        """Compute the effective approver list at the current tier — applies OOO + delegation."""
        self.ensure_one()
        if not self.current_tier_id:
            self.pending_approver_ids = [(5, 0, 0)]
            return
        raw = self.current_tier_id._resolve_approvers(self._record())
        Delegation = self.env["approval.delegation"].sudo()
        OOO = self.env["approval.ooo"].sudo()
        effective = self.env["res.users"]
        for u in raw:
            # 1. Active OOO with auto_delegate_to → delegate target
            ooo = OOO._active_for(u)
            if ooo and ooo.auto_delegate_to_id:
                effective |= ooo.auto_delegate_to_id
                continue
            # 2. Active delegation
            delegation = Delegation._find_delegated_to(user=u, model_name=self.res_model)
            if delegation:
                effective |= delegation.delegate_to_id
                continue
            effective |= u
        self.pending_approver_ids = [(6, 0, effective.ids)]

    def _record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return self.env["res.partner"].browse()
        return self.env[self.res_model].sudo().browse(self.res_id)

    def _notify_pending(self):
        self.ensure_one()
        if not self.pending_approver_ids:
            return
        template = self.env.ref("custom_approval_engine.mail_template_approval_pending", raise_if_not_found=False)
        if not template:
            return
        # Notification is best-effort: the lifecycle decision is already
        # persisted before we get here, so an ACL hiccup during template
        # render (e.g. acting user can't read res.partner) must not roll back.
        for u in self.pending_approver_ids:
            try:
                template.with_context(approver=u).sudo().send_mail(self.id, force_send=False)
            except Exception:
                _logger.exception("approval %s: send_mail to %s failed (non-fatal)", self.id, u.login)

    # ---------------------------------------------------------------- escalation cron

    @api.model
    def _cron_check_escalations(self):
        """Called by ``ir.cron`` every 15 minutes.

        Use a Python-side filter rather than the SQL `_search_overdue` so the
        comparison reads the ORM cache. This matters for in-transaction
        callers (tests, manual triggers) where due_at writes may not have
        been flushed yet — the SQL search would otherwise miss them.
        """
        now = fields.Datetime.now()
        candidates = self.sudo().search([("state", "=", "pending")])
        overdue = candidates.filtered(lambda r: r.due_at and r.due_at < now)
        for rec in overdue:
            try:
                rec._handle_overdue()
            except Exception:
                _logger.exception("approval.request._handle_overdue failed id=%s", rec.id)

    def _handle_overdue(self):
        self.ensure_one()
        tier = self.current_tier_id
        if not tier:
            return
        action = tier.on_overdue
        if action == "auto_approve":
            self._record_line(tier, self.env.ref("base.user_root"), "approved", "Auto-approved on SLA breach")
            self._advance_to_next_tier()
        elif action == "escalate_to_next":
            self._record_line(tier, self.env.ref("base.user_root"), "escalated", "Escalated to next tier on SLA breach")
            self._advance_to_next_tier()
        elif action == "escalate_to_user":
            target = tier.escalation_user_id
            if not target:
                _logger.warning("approval.request %s overdue, escalation_user missing", self.id)
                return
            self.write(
                {
                    "pending_approver_ids": [(6, 0, [target.id])],
                    "due_at": fields.Datetime.now() + timedelta(hours=tier.sla_hours),
                }
            )
            self._record_line(
                tier, self.env.ref("base.user_root"), "escalated", f"Escalated to fallback approver {target.name}"
            )
            self._notify_pending()
        # action == 'none' → just notify (re-send pending notice)
        elif action == "none":
            self._notify_pending()
        self._pdp_audit_write("approval_overdue", self.id, {"action": action})
