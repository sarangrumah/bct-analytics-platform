# -*- coding: utf-8 -*-
"""dev.cycle — implementation lifecycle tracker for a BRD recommendation."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


STATE_SEQUENCE = [
    "backlog",
    "in_dev",
    "code_review",
    "qa",
    "uat",
    "deployed",
    "done",
]


class DevCycle(models.Model):
    _name = "dev.cycle"
    _description = "Development Cycle"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, tracking=True)

    journey_id = fields.Many2one(
        comodel_name="onboarding.journey",
        string="Onboarding Journey",
        ondelete="set null",
        index=True,
    )
    brd_recommendation_id = fields.Many2one(
        comodel_name="brd.recommendation",
        string="BRD Recommendation",
        ondelete="set null",
        index=True,
    )
    module_target_id = fields.Many2one(
        comodel_name="custom.hub.module.catalog",
        string="Target Hub Module",
        ondelete="set null",
        index=True,
    )

    env_progress = fields.Selection(
        [
            ("dev", "Dev"),
            ("staging", "Staging"),
            ("uat", "UAT"),
            ("prod", "Production"),
        ],
        default="dev",
        tracking=True,
    )

    branch_name = fields.Char(
        compute="_compute_branch_suggestion",
        store=True,
        readonly=False,
        tracking=True,
    )
    repo_url = fields.Char(tracking=True)

    state = fields.Selection(
        [
            ("backlog", "Backlog"),
            ("in_dev", "In Development"),
            ("code_review", "Code Review"),
            ("qa", "QA"),
            ("uat", "UAT"),
            ("deployed", "Deployed"),
            ("done", "Done"),
        ],
        default="backlog",
        tracking=True,
        index=True,
    )

    assignee_id = fields.Many2one("res.users", string="Assignee", tracking=True)

    estimate_md = fields.Float(string="Estimate (MD)")
    actual_md = fields.Float(
        string="Actual (MD)",
        compute="_compute_actual_md",
        store=True,
        readonly=False,
    )

    project_task_id = fields.Many2one(
        "project.task",
        string="Project Task",
        ondelete="set null",
        copy=False,
    )

    created_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)

    pr_ids = fields.One2many("dev.cycle.pr", "cycle_id", string="Pull Requests")
    deployment_ids = fields.One2many("dev.cycle.deployment", "cycle_id", string="Deployments")
    pr_count = fields.Integer(compute="_compute_pr_count")
    deployment_count = fields.Integer(compute="_compute_deployment_count")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("brd_recommendation_id", "brd_recommendation_id.name")
    def _compute_branch_suggestion(self):
        for rec in self:
            if rec.branch_name:
                continue
            br = rec.brd_recommendation_id
            if br:
                slug = re.sub(r"[^a-z0-9]+", "-", (br.name or "").lower()).strip("-")
                rec.branch_name = f"feature/brd-{br.id}-{slug or 'change'}"
            else:
                rec.branch_name = False

    @api.depends("pr_ids")
    def _compute_pr_count(self):
        for rec in self:
            rec.pr_count = len(rec.pr_ids)

    @api.depends("deployment_ids")
    def _compute_deployment_count(self):
        for rec in self:
            rec.deployment_count = len(rec.deployment_ids)

    @api.depends("project_task_id")
    def _compute_actual_md(self):
        # Default: keep manual value. If linked task has effective hours,
        # convert (8h/MD). Manual override remains because readonly=False.
        for rec in self:
            task = rec.project_task_id
            if task and "effective_hours" in task._fields and task.effective_hours:
                rec.actual_md = task.effective_hours / 8.0
            elif not rec.actual_md:
                rec.actual_md = 0.0

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def action_transition_state(self, new_state):
        if new_state not in dict(self._fields["state"].selection):
            raise UserError(_("Unknown dev.cycle state: %s") % new_state)
        for rec in self:
            cur_idx = STATE_SEQUENCE.index(rec.state)
            new_idx = STATE_SEQUENCE.index(new_state)
            # Allow forward moves of any length, allow 1-step rollback.
            if new_idx < cur_idx - 1:
                raise UserError(
                    _("Cannot jump from %(cur)s back to %(new)s (more than one step).")
                    % {"cur": rec.state, "new": new_state}
                )
            vals = {"state": new_state}
            if new_state == "in_dev" and not rec.started_at:
                vals["started_at"] = fields.Datetime.now()
            if new_state == "done" and not rec.completed_at:
                vals["completed_at"] = fields.Datetime.now()
            rec.write(vals)
            rec.message_post(body=_("State transitioned: %(old)s → %(new)s") % {"old": rec.state, "new": new_state})
        return True

    def action_start(self):
        return self.action_transition_state("in_dev")

    def action_to_review(self):
        return self.action_transition_state("code_review")

    def action_to_qa(self):
        return self.action_transition_state("qa")

    def action_to_uat(self):
        return self.action_transition_state("uat")

    def action_deploy(self):
        return self.action_transition_state("deployed")

    def action_done(self):
        return self.action_transition_state("done")

    # ------------------------------------------------------------------
    # Smart buttons / actions
    # ------------------------------------------------------------------

    def action_open_pr_list(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pull Requests"),
            "res_model": "dev.cycle.pr",
            "view_mode": "list,form",
            "domain": [("cycle_id", "=", self.id)],
            "context": {"default_cycle_id": self.id},
        }

    def action_open_deployment_list(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deployments"),
            "res_model": "dev.cycle.deployment",
            "view_mode": "list,form",
            "domain": [("cycle_id", "=", self.id)],
            "context": {"default_cycle_id": self.id},
        }

    def action_create_project_task(self):
        Project = self.env["project.project"].sudo()
        Task = self.env["project.task"].sudo()
        for rec in self:
            if rec.project_task_id:
                continue
            project = False
            # Try to source project from journey if it exposes one.
            j = rec.journey_id
            if j and "project_id" in j._fields and j.project_id:
                project = j.project_id
            if not project:
                project = Project.search([("name", "=", "Dev Cycle Tasks")], limit=1)
                if not project:
                    project = Project.create({"name": "Dev Cycle Tasks"})
            task_vals = {
                "name": rec.name,
                "project_id": project.id,
                "description": (
                    f"<p><b>Branch:</b> {rec.branch_name or '—'}</p>"
                    f"<p><b>Repo:</b> {rec.repo_url or '—'}</p>"
                    f"<p><b>BRD:</b> {rec.brd_recommendation_id.display_name or '—'}</p>"
                ),
            }
            if rec.assignee_id:
                task_vals["user_ids"] = [(6, 0, [rec.assignee_id.id])]
            task = Task.create(task_vals)
            rec.project_task_id = task.id
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") and vals.get("brd_recommendation_id"):
                br = self.env["brd.recommendation"].browse(vals["brd_recommendation_id"])
                vals["name"] = _("Dev Cycle: %s") % (br.name or br.display_name or "")
        return super().create(vals_list)
