# -*- coding: utf-8 -*-
"""Project delta: vertical, portfolio, health, WIP limit."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "pdp.audited.mixin"]

    custom_vertical_id = fields.Many2one(
        "custom.project.vertical",
        string="Vertical",
        index=True,
    )
    custom_portfolio_id = fields.Many2one(
        "custom.project.portfolio",
        string="Portfolio",
        index=True,
    )
    custom_po_id = fields.Many2one("res.users", string="Product Owner")
    custom_ba_id = fields.Many2one("res.users", string="Business Analyst")
    custom_code = fields.Char(string="Project Code", help="e.g. PRJ-2026-014")
    custom_health = fields.Selection(
        [
            ("on_track", "On track"),
            ("at_risk", "At risk"),
            ("blocked", "Blocked"),
        ],
        default="on_track",
        required=True,
        tracking=True,
    )
    custom_health_note = fields.Text(
        help="Why the health is what it is. Required once health leaves on-track.",
    )
    custom_wip_limit = fields.Integer(
        string="WIP Limit",
        default=0,
        help="Maximum in-flight tasks per active stage. 0 disables the check.",
    )
    custom_progress = fields.Float(
        string="Progress (%)",
        compute="_compute_progress",
        store=True,
        digits=(5, 1),
    )
    custom_task_overdue_count = fields.Integer(
        compute="_compute_progress",
        store=True,
        string="Overdue Tasks",
    )
    custom_task_hold_count = fields.Integer(
        compute="_compute_progress",
        store=True,
        string="Tasks On Hold",
    )
    custom_task_waiting_user_count = fields.Integer(
        compute="_compute_progress",
        store=True,
        string="Tasks Awaiting User",
    )

    @api.depends(
        "task_ids.stage_id",
        "task_ids.custom_due_sla_date",
        "task_ids.stage_id.custom_is_closed_stage",
    )
    def _compute_progress(self):
        now = fields.Datetime.now()
        for project in self:
            tasks = project.task_ids
            total = len(tasks)
            done = len(tasks.filtered(lambda t: t.stage_id.custom_is_closed_stage))
            project.custom_progress = round(done * 100.0 / total, 1) if total else 0.0
            project.custom_task_overdue_count = len(
                tasks.filtered(
                    lambda t: (
                        t.custom_due_sla_date
                        and t.custom_due_sla_date < now
                        and not t.stage_id.custom_is_closed_stage
                        and t.stage_id.custom_sla_clock == "running"
                    )
                )
            )
            project.custom_task_hold_count = len(tasks.filtered(lambda t: t.stage_id.custom_is_hold))
            project.custom_task_waiting_user_count = len(tasks.filtered(lambda t: t.stage_id.custom_is_waiting_user))

    @api.constrains("custom_health", "custom_health_note")
    def _check_health_note(self):
        for project in self:
            if project.custom_health != "on_track" and not project.custom_health_note:
                raise ValidationError(
                    _(
                        "Project %s is no longer on track — say why. A red light with no "
                        "explanation cannot be acted on.",
                        project.name,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        projects = super().create(vals_list)
        # Attach the VAS stage set to every new project. Odoo only shows a stage in a
        # project's kanban when it is linked to that project, so a globally seeded stage
        # set is not enough on its own.
        vas_stages = self.env["project.task.type"].search([("custom_code", "!=", False)])
        if vas_stages:
            for project in projects:
                vas_stages.write({"project_ids": [(4, project.id)]})
        return projects

    def write(self, vals):
        health_before = {p.id: p.custom_health for p in self}
        result = super().write(vals)
        if "custom_health" in vals:
            rank = {"on_track": 0, "at_risk": 1, "blocked": 2}
            for project in self:
                before = health_before.get(project.id)
                if before and rank.get(project.custom_health, 0) > rank.get(before, 0):
                    project._pdp_audit_write(
                        "health_degraded",
                        project.id,
                        {"custom_health": [before, project.custom_health]},
                        reason=project.custom_health_note,
                    )
                    project._pmo_notify_project_event("health_degraded")
        return result

    def _pmo_notify_project_event(self, event, extra=None):
        """No-op hook; ``custom_project_notify`` overrides it."""
        return True
