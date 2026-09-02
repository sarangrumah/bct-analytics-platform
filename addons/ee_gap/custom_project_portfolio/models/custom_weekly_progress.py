# -*- coding: utf-8 -*-
"""Weekly progress report.

Half of this record writes itself. The cron drafts one row per active project (and per
active change request once ``custom_project_cr`` is installed) every Friday afternoon,
already filled with what happened: tasks closed, work carried over, hours booked, cycle
time. The Business Analyst only supplies what a machine cannot know -- the blocker and
next week's intent.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomWeeklyProgress(models.Model):
    _name = "custom.weekly.progress"
    _description = "VAS Weekly Progress Update"
    _inherit = ["pdp.audited.mixin"]
    _order = "sprint_id desc, vertical_id, id"

    sprint_id = fields.Many2one(
        "custom.project.sprint",
        string="Sprint",
        required=True,
        ondelete="cascade",
        index=True,
    )
    week_code = fields.Char(related="sprint_id.week_code", store=True, readonly=True)
    vertical_id = fields.Many2one("custom.project.vertical", string="Vertical", index=True)
    project_id = fields.Many2one("project.project", string="Project", index=True)
    author_id = fields.Many2one(
        "res.users",
        string="Business Analyst",
        default=lambda self: self.env.user,
    )

    progress_pct = fields.Float(string="Progress (%)", digits=(5, 1))
    health = fields.Selection(
        [("on_track", "On track"), ("at_risk", "At risk"), ("blocked", "Blocked")],
        default="on_track",
    )

    # -- automatic half -------------------------------------------------
    done_task_ids = fields.Many2many(
        "project.task",
        "custom_weekly_done_rel",
        "weekly_id",
        "task_id",
        string="Closed This Week",
        readonly=True,
    )
    done_count = fields.Integer(readonly=True)
    done_points = fields.Integer(readonly=True)
    carry_over_count = fields.Integer(readonly=True)
    hours_spent = fields.Float(readonly=True, digits=(10, 2))
    cycle_time_team = fields.Float(readonly=True, digits=(10, 2))
    lead_time_total = fields.Float(readonly=True, digits=(10, 2))
    hold_count = fields.Integer(readonly=True)
    waiting_user_count = fields.Integer(readonly=True)

    # -- narrative half -------------------------------------------------
    plan_this_week = fields.Text(string="Plan (this week)")
    blocker = fields.Text(
        string="Blocker",
        help="What is in the way, and who has to act. Auto-seeded from long holds.",
    )
    next_week = fields.Text(string="Plan (next week)")

    state = fields.Selection(
        [("draft", "Draft"), ("submitted", "Submitted"), ("reviewed", "Reviewed")],
        default="draft",
        required=True,
    )
    submitted_at = fields.Datetime(readonly=True)

    _weekly_uniq = models.Constraint(
        "unique(sprint_id, project_id)",
        "This project already has a weekly report for that sprint.",
    )

    @api.depends("week_code", "project_id", "vertical_id")
    def _compute_display_name(self):
        for rec in self:
            parts = [rec.week_code or "?"]
            if rec.vertical_id:
                parts.append(rec.vertical_id.code)
            if rec.project_id:
                parts.append(rec.project_id.name)
            rec.display_name = " · ".join(parts)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def action_refresh_automatic(self):
        """Recompute the machine-written half from the sprint window."""
        for rec in self:
            rec._fill_automatic()
        return True

    def _fill_automatic(self):
        self.ensure_one()
        sprint = self.sprint_id
        start = fields.Datetime.to_datetime(sprint.date_start)
        end = fields.Datetime.to_datetime(sprint.date_end).replace(hour=23, minute=59, second=59)
        task_model = self.env["project.task"]
        domain = [("custom_sprint_id", "=", sprint.id)]
        if self.project_id:
            domain.append(("project_id", "=", self.project_id.id))
        elif self.vertical_id:
            domain.append(("custom_vertical_id", "=", self.vertical_id.id))
        tasks = task_model.search(domain)

        closed = tasks.filtered(lambda t: t.custom_closed_at and start <= t.custom_closed_at <= end)
        open_tasks = tasks - closed
        holds = tasks.filtered(lambda t: t.stage_id.custom_is_hold)
        waiting = tasks.filtered(lambda t: t.stage_id.custom_is_waiting_user)

        hours = 0.0
        if closed or open_tasks:
            lines = self.env["account.analytic.line"].search(
                [
                    ("task_id", "in", tasks.ids),
                    ("date", ">=", sprint.date_start),
                    ("date", "<=", sprint.date_end),
                ]
            )
            hours = sum(lines.mapped("unit_amount"))

        cycle = sum(closed.mapped("custom_cycle_time_team")) / len(closed) if closed else 0.0
        lead = sum(closed.mapped("custom_lead_time_total")) / len(closed) if closed else 0.0

        blocker_seed = self.blocker
        if not blocker_seed and holds:
            blocker_seed = "\n".join(
                _(
                    "%(name)s on hold since %(since)s — %(reason)s",
                    name=task.name,
                    since=fields.Date.to_string(task.custom_hold_since.date()) if task.custom_hold_since else "?",
                    reason=(task.custom_hold_reason or _("no reason recorded")).strip(),
                )
                for task in holds
            )

        self.write(
            {
                "done_task_ids": [(6, 0, closed.ids)],
                "done_count": len(closed),
                "done_points": sum(closed.mapped("custom_story_points")),
                "carry_over_count": len(open_tasks),
                "hours_spent": round(hours, 2),
                "cycle_time_team": round(cycle, 2),
                "lead_time_total": round(lead, 2),
                "hold_count": len(holds),
                "waiting_user_count": len(waiting),
                "progress_pct": self.project_id.custom_progress if self.project_id else 0.0,
                "health": self.project_id.custom_health if self.project_id else self.health,
                "blocker": blocker_seed,
            }
        )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for rec in self:
            if not rec.next_week:
                raise UserError(_("Fill in next week's plan for %s before submitting.", rec.display_name))
            rec.write({"state": "submitted", "submitted_at": fields.Datetime.now()})
            rec._pdp_audit_write("weekly_submit", rec.id, {"state": "submitted"})
            rec._pmo_notify_weekly_event("weekly_submitted")
        return True

    def _pmo_notify_weekly_event(self, event, extra=None):
        """No-op hook; ``custom_project_notify`` overrides it."""
        return True

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------

    @api.model
    def cron_draft_weekly(self):
        """Friday 15:00 — draft a report per active project, pre-filled.

        Idempotent by the (sprint, project) unique constraint: an existing row is
        refreshed rather than duplicated.
        """
        sprint = self.env["custom.project.sprint"].current_sprint()
        projects = self.env["project.project"].search([("active", "=", True)])
        created = refreshed = 0
        for project in projects:
            existing = self.search(
                [
                    ("sprint_id", "=", sprint.id),
                    ("project_id", "=", project.id),
                ],
                limit=1,
            )
            if existing:
                if existing.state == "draft":
                    existing._fill_automatic()
                    refreshed += 1
                continue
            record = self.create(
                {
                    "sprint_id": sprint.id,
                    "project_id": project.id,
                    "vertical_id": project.custom_vertical_id.id,
                    "author_id": (project.custom_ba_id or project.custom_po_id).id or self.env.uid,
                }
            )
            record._fill_automatic()
            created += 1
        _logger.info(
            "VAS PMO: weekly draft for %s — %s created, %s refreshed",
            sprint.week_code,
            created,
            refreshed,
        )
        # Nudge whoever has not written anything yet.
        pending = self.search([("sprint_id", "=", sprint.id), ("state", "=", "draft")])
        for record in pending:
            record._pmo_notify_weekly_event("weekly_reminder")

    @api.model
    def cron_weekly_digest(self):
        """Monday 08:00 — send the recap of the sprint that just closed."""
        sprint = self.env["custom.project.sprint"].search(
            [("state", "=", "closed")],
            order="date_start desc",
            limit=1,
        )
        if not sprint:
            return
        reports = self.search([("sprint_id", "=", sprint.id)])
        if not reports:
            return
        reports[:1]._pmo_notify_weekly_event("weekly_digest")
