# -*- coding: utf-8 -*-
"""Weekly sprint. One ISO week, opened and closed by cron -- no manual ritual."""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class CustomProjectSprint(models.Model):
    _name = "custom.project.sprint"
    _description = "VAS Weekly Sprint"
    _inherit = ["pdp.audited.mixin"]
    _order = "date_start desc"
    _rec_name = "week_code"

    week_code = fields.Char(
        required=True,
        help="ISO week key, e.g. 2026-W31.",
    )
    date_start = fields.Date(required=True, help="Monday of the week.")
    date_end = fields.Date(required=True, help="Friday of the week.")
    goal = fields.Text()
    state = fields.Selection(
        [("planned", "Planned"), ("active", "Active"), ("closed", "Closed")],
        default="planned",
        required=True,
    )
    capacity_points = fields.Integer(
        default=0,
        help="Team capacity for the week, in story points.",
    )

    task_ids = fields.One2many("project.task", "custom_sprint_id", string="Tasks")
    weekly_ids = fields.One2many("custom.weekly.progress", "sprint_id", string="Weekly Reports")

    done_points = fields.Integer(compute="_compute_points", store=False)
    committed_points = fields.Integer(compute="_compute_points", store=False)
    carry_over_count = fields.Integer(compute="_compute_points", store=False)

    _week_uniq = models.Constraint(
        "unique(week_code)",
        "This ISO week already has a sprint.",
    )

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for rec in self:
            if rec.date_end < rec.date_start:
                raise ValidationError(_("Sprint end date cannot precede its start date."))

    @api.depends("task_ids.custom_story_points", "task_ids.stage_id")
    def _compute_points(self):
        for rec in self:
            done = committed = carried = 0
            for task in rec.task_ids:
                points = task.custom_story_points or 0
                committed += points
                if task.stage_id.custom_is_closed_stage:
                    done += points
                else:
                    carried += 1
            rec.done_points = done
            rec.committed_points = committed
            rec.carry_over_count = carried

    # ------------------------------------------------------------------
    # Week helpers
    # ------------------------------------------------------------------

    @api.model
    def _week_code_for(self, day):
        iso = day.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    @api.model
    def _get_or_create_week(self, day=None):
        """Return the sprint covering ``day`` (default: today), creating it if needed."""
        day = day or fields.Date.context_today(self)
        monday = day - timedelta(days=day.weekday())
        friday = monday + timedelta(days=4)
        code = self._week_code_for(monday)
        sprint = self.search([("week_code", "=", code)], limit=1)
        if not sprint:
            sprint = self.create(
                {
                    "week_code": code,
                    "date_start": monday,
                    "date_end": friday,
                    "state": "active",
                }
            )
        return sprint

    @api.model
    def current_sprint(self):
        return self._get_or_create_week()

    # ------------------------------------------------------------------
    # Cron: close Friday, open the next week, carry work forward
    # ------------------------------------------------------------------

    @api.model
    def cron_roll_sprint(self):
        """Close the active sprint and open the next one.

        Scheduled Friday 18:00. Idempotent: running twice on the same day is a no-op
        because the closed sprint is no longer 'active'.
        """
        today = fields.Date.context_today(self)
        active = self.search([("state", "=", "active")])
        for sprint in active:
            if today <= sprint.date_end:
                continue  # not finished yet
            unfinished = sprint.task_ids.filtered(lambda task: not task.stage_id.custom_is_closed_stage)
            # Monday of the week AFTER the one that just ended. Computed from the weekday
            # rather than "+3 days" so a cron that fires late still lands on the next
            # week instead of re-opening the one it just closed.
            next_monday = sprint.date_end + timedelta(days=7 - sprint.date_end.weekday())
            next_sprint = self._get_or_create_week(next_monday)
            if unfinished:
                unfinished.write(
                    {
                        "custom_sprint_id": next_sprint.id,
                        "custom_carried_over": True,
                    }
                )
            sprint.write({"state": "closed"})
            sprint._pdp_audit_write(
                "sprint_close",
                sprint.id,
                {"state": "closed", "carry_over": len(unfinished)},
                reason=_("Automatic weekly roll-over"),
            )
            _logger.info(
                "VAS PMO: sprint %s closed, %s task(s) carried into %s",
                sprint.week_code,
                len(unfinished),
                next_sprint.week_code,
            )
        # Make sure the current week exists and is active even if nothing was open.
        self._get_or_create_week().filtered(lambda s: s.state == "planned").write({"state": "active"})
