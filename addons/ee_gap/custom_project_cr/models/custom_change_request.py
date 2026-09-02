# -*- coding: utf-8 -*-
"""Change request: the brand's ask, from intake to verified."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Response SLA in working days, counted from the moment the request arrived.
RESPONSE_SLA_DAYS = {"critical": 1, "high": 1, "medium": 2, "low": 5}

# Impact levels that need the vertical owner as a third approver.
THIRD_TIER_IMPACT = ("high", "critical")


class CustomChangeRequest(models.Model):
    _name = "custom.change.request"
    _description = "VAS Change Request"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        readonly=True,
        copy=False,
        index=True,
        help="Official number quoted in correspondence with the brand (CR-YYYY-NNNN).",
    )
    active = fields.Boolean(default=True)

    vertical_id = fields.Many2one(
        "custom.project.vertical",
        string="Vertical",
        required=True,
        index=True,
        tracking=True,
    )
    project_id = fields.Many2one(
        "project.project",
        string="Project",
        help="Optional. A change request can exist without a project behind it.",
    )
    requester_partner_id = fields.Many2one(
        "res.partner",
        string="Requested By",
        tracking=True,
        help="Who asked, on the brand side.",
    )
    request_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
        help="When the brand asked. The response SLA runs from here, not from when work started.",
    )

    cr_type = fields.Selection(
        [
            ("enhancement", "Enhancement"),
            ("bug", "Bug"),
            ("config", "Configuration"),
            ("data_fix", "Data fix"),
            ("new_feature", "New feature"),
        ],
        string="Type",
        default="enhancement",
        required=True,
        tracking=True,
    )
    priority = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
        default="medium",
        required=True,
        tracking=True,
    )
    impact = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
        default="medium",
        required=True,
        tracking=True,
        help="Blast radius if this goes wrong. Drives how many approval tiers are needed.",
    )

    # -- impact analysis: the reason this is not a task ------------------
    description = fields.Html(string="Request")
    impact_analysis = fields.Html(string="Impact Analysis")
    affected_modules = fields.Char(help="Comma-separated technical module names.")
    effort_estimate_days = fields.Float(string="Effort Estimate (days)", digits=(6, 2))
    risk_note = fields.Text()
    rollback_plan = fields.Text()
    need_downtime = fields.Boolean(string="Downtime Required")

    ba_id = fields.Many2one("res.users", string="Business Analyst", tracking=True)
    po_id = fields.Many2one("res.users", string="Product Owner", tracking=True)

    stage_id = fields.Many2one(
        "project.task.type",
        string="Stage",
        domain="[('custom_applies_to','in',['cr','both'])]",
        tracking=True,
    )
    approval_state = fields.Selection(
        [
            ("draft", "Intake"),
            ("analysis", "Analysis"),
            ("waiting_approval", "Waiting approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    approval_ids = fields.One2many(
        "custom.change.request.approval",
        "request_id",
        string="Approvals",
    )
    approval_progress = fields.Char(compute="_compute_approval_progress")
    reject_reason = fields.Text()

    sla_response_due = fields.Datetime(compute="_compute_sla_due", store=True)
    sla_response_met = fields.Boolean(readonly=True)
    first_response_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)

    task_ids = fields.One2many("project.task", "change_request_id", string="Tasks")
    task_count = fields.Integer(compute="_compute_task_stats")
    task_done_count = fields.Integer(compute="_compute_task_stats")

    _code_uniq = models.Constraint(
        "unique(code)",
        "This change-request number already exists.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("request_date", "priority")
    def _compute_sla_due(self):
        task_model = self.env["project.task"]
        for rec in self:
            if not rec.request_date:
                rec.sla_response_due = False
                continue
            days = RESPONSE_SLA_DAYS.get(rec.priority, 2)
            rec.sla_response_due = task_model._pmo_add_working_days(rec.request_date, days)

    @api.depends("task_ids.stage_id")
    def _compute_task_stats(self):
        for rec in self:
            rec.task_count = len(rec.task_ids)
            rec.task_done_count = len(rec.task_ids.filtered(lambda t: t.stage_id.custom_is_closed_stage))

    @api.depends("approval_ids.state", "impact")
    def _compute_approval_progress(self):
        for rec in self:
            done = len(rec.approval_ids.filtered(lambda a: a.state == "approved"))
            rec.approval_progress = f"{done}/{rec._required_tiers()}"

    def _required_tiers(self):
        self.ensure_one()
        return 3 if self.impact in THIRD_TIER_IMPACT else 2

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code"):
                vals["code"] = self.env["ir.sequence"].next_by_code("custom.change.request") or "CR-NEW"
            if not vals.get("stage_id"):
                intake = self.env["project.task.type"]._stage_by_code("backlog")
                if intake:
                    vals["stage_id"] = intake.id
            if not vals.get("ba_id") and vals.get("vertical_id"):
                vertical = self.env["custom.project.vertical"].browse(vals["vertical_id"])
                if vertical.ba_ids:
                    vals["ba_id"] = vertical.ba_ids[0].id
            if not vals.get("po_id") and vals.get("vertical_id"):
                vertical = self.env["custom.project.vertical"].browse(vals["vertical_id"])
                if vertical.vertical_po_id:
                    vals["po_id"] = vertical.vertical_po_id.id
        records = super().create(vals_list)
        for record in records:
            record._cr_notify_event("cr_created")
        return records

    def write(self, vals):
        stage_before = {rec.id: rec.stage_id for rec in self}
        result = super().write(vals)
        if "stage_id" in vals:
            for rec in self:
                old = stage_before.get(rec.id)
                rec._pdp_audit_write(
                    "stage_change",
                    rec.id,
                    {"stage_id": [old.name if old else None, rec.stage_id.name]},
                )
                if rec.stage_id.custom_is_waiting_user:
                    rec._cr_notify_event("verify_request")
                elif rec.stage_id.custom_is_closed_stage:
                    rec.closed_at = fields.Datetime.now()
                    rec._cr_notify_event("cr_closed")
        return result

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.code} — {rec.name}" if rec.code else rec.name

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("approval_state", "impact_analysis", "effort_estimate_days")
    def _check_analysis_before_approval(self):
        for rec in self:
            if rec.approval_state in ("waiting_approval", "approved"):
                if not rec.impact_analysis:
                    raise ValidationError(
                        _(
                            "%s cannot go for approval without an impact analysis — that "
                            "analysis is the reason a change request exists.",
                            rec.code,
                        )
                    )
                if not rec.effort_estimate_days:
                    raise ValidationError(_("%s needs an effort estimate before approval.", rec.code))

    @api.constrains("approval_state", "reject_reason")
    def _check_reject_reason(self):
        for rec in self:
            if rec.approval_state == "rejected" and not rec.reject_reason:
                raise ValidationError(_("Rejecting %s requires a reason.", rec.code))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def _stamp_first_response(self):
        for rec in self:
            if not rec.first_response_at:
                now = fields.Datetime.now()
                rec.write(
                    {
                        "first_response_at": now,
                        "sla_response_met": bool(rec.sla_response_due and now <= rec.sla_response_due),
                    }
                )

    def action_start_analysis(self):
        """Triage decision: this request is real, a BA picks it up."""
        for rec in self:
            if rec.approval_state != "draft":
                raise UserError(_("%s has already left intake.", rec.code))
            analysis_stage = self.env["project.task.type"]._stage_by_code("analysis")
            rec.write(
                {
                    "approval_state": "analysis",
                    "ba_id": rec.ba_id.id or self.env.uid,
                    "stage_id": analysis_stage.id or rec.stage_id.id,
                }
            )
            rec._stamp_first_response()
            rec._pdp_audit_write("cr_triage", rec.id, {"approval_state": "analysis"})
            rec._cr_notify_event("cr_analysis")
        return True

    def action_submit_for_approval(self):
        for rec in self:
            if rec.approval_state != "analysis":
                raise UserError(_("Only an analysed request can go for approval."))
            rec.approval_ids.unlink()
            rec._build_approval_chain()
            rec.write({"approval_state": "waiting_approval"})
            rec._pdp_audit_write("cr_submit", rec.id, {"approval_state": "waiting_approval"})
            rec._cr_notify_event("cr_submit")
        return True

    def _build_approval_chain(self):
        self.ensure_one()
        lines = [
            {"tier": 1, "role": "ba", "user_id": (self.ba_id or self.env.user).id},
            {"tier": 2, "role": "po", "user_id": (self.po_id or self.env.user).id},
        ]
        if self.impact in THIRD_TIER_IMPACT:
            owner = self.vertical_id.vertical_po_id or self.po_id or self.env.user
            lines.append({"tier": 3, "role": "vertical_owner", "user_id": owner.id})
        self.env["custom.change.request.approval"].create([dict(line, request_id=self.id) for line in lines])
        self._cr_external_approval_hook()

    def _cr_external_approval_hook(self):
        """Where a ``custom_approval_engine`` request would be raised instead."""
        return True

    def action_approve(self):
        """Approve the caller's own pending tier."""
        for rec in self:
            line = rec.approval_ids.filtered(lambda a: a.state == "pending" and a.user_id == self.env.user)[:1]
            if not line:
                line = rec.approval_ids.filtered(lambda a: a.state == "pending")[:1]
            if not line:
                raise UserError(_("Nothing pending on %s.", rec.code))
            line.action_approve()
        return True

    def action_reject(self):
        for rec in self:
            if not rec.reject_reason:
                raise UserError(_("Say why %s is rejected — the brand will ask.", rec.code))
            rec.approval_ids.filtered(lambda a: a.state == "pending").write({"state": "rejected"})
            rec.write({"approval_state": "rejected"})
            rec._pdp_audit_write(
                "cr_reject",
                rec.id,
                {"approval_state": "rejected"},
                reason=rec.reject_reason,
            )
            rec._cr_notify_event("cr_reject")
        return True

    def _on_fully_approved(self):
        self.ensure_one()
        self.write({"approval_state": "approved"})
        self._pdp_audit_write("cr_approve", self.id, {"approval_state": "approved"})
        self._cr_notify_event("cr_approve")

    def action_spawn_tasks(self):
        """Turn an approved request into work."""
        self.ensure_one()
        if self.approval_state != "approved":
            raise UserError(_("Only an approved request can spawn tasks."))
        if not self.project_id:
            raise UserError(_("Point %s at a project first — a task needs somewhere to live.", self.code))
        dev_stage = self.env["project.task.type"]._stage_by_code("analysis")
        task = self.env["project.task"].create(
            {
                "name": f"{self.code} — {self.name}",
                "project_id": self.project_id.id,
                "change_request_id": self.id,
                "custom_vertical_id": self.vertical_id.id,
                "custom_source": "cr",
                "custom_priority": self.priority,
                "stage_id": dev_stage.id if dev_stage else False,
                "user_ids": [(6, 0, self.ba_id.ids)] if self.ba_id else False,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": task.id,
            "view_mode": "form",
        }

    def action_request_verification(self):
        self.ensure_one()
        stage = self.env["project.task.type"]._stage_by_code("waiting_user")
        if not stage:
            raise UserError(_("No Waiting-User-Verification stage is configured."))
        self.write({"stage_id": stage.id})
        return True

    def _cr_notify_event(self, event, extra=None):
        """No-op hook; ``custom_project_notify`` overrides it."""
        return True

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def cron_intake_sla(self):
        """Flag intake that nobody has triaged inside the response SLA."""
        now = fields.Datetime.now()
        stale = self.search(
            [
                ("approval_state", "=", "draft"),
                ("sla_response_due", "<", now),
                ("first_response_at", "=", False),
            ]
        )
        for rec in stale:
            rec._cr_notify_event("cr_intake_overdue")
        if stale:
            _logger.info("VAS PMO: %s change request(s) past their response SLA", len(stale))
