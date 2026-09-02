# -*- coding: utf-8 -*-
"""Task delta: brand vertical, weekly sprint, and the Hold / Waiting-User clock.

The interesting part of this file is not the extra fields -- it is
``_pmo_apply_stage_clock``. Every stage transition books the time just spent into
one of three buckets (team, hold, user side), which is what lets the team be measured
on time it actually owned.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

TASK_SOURCES = [
    ("po", "Product Owner"),
    ("cr", "Change Request"),
    ("ticket", "Ticket"),
    ("jira", "Jira"),
]

PRIORITY_LEVELS = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("critical", "Critical"),
]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "pdp.audited.mixin"]

    # ---------------------------------------------------------------- context
    custom_vertical_id = fields.Many2one(
        "custom.project.vertical",
        string="Vertical",
        index=True,
        help="Brand this work belongs to. Inherited from the project or change request; "
        "override it only for genuinely cross-brand work.",
    )
    custom_vertical_override = fields.Boolean(
        string="Vertical Overridden",
        help="Set when the vertical intentionally differs from the parent's.",
    )
    custom_vertical_override_reason = fields.Char(string="Override Reason")
    custom_portfolio_id = fields.Many2one(
        "custom.project.portfolio",
        string="Portfolio",
        related="project_id.custom_portfolio_id",
        store=True,
        readonly=True,
    )
    custom_sprint_id = fields.Many2one("custom.project.sprint", string="Sprint", index=True)
    custom_carried_over = fields.Boolean(
        string="Carried Over",
        help="Set by the weekly roll-over when the task did not finish in its sprint.",
    )

    custom_task_type = fields.Selection(
        [("feature", "Feature"), ("bug", "Bug"), ("spike", "Spike")],
        string="Task Type",
        default="feature",
    )
    custom_priority = fields.Selection(
        PRIORITY_LEVELS,
        string="VAS Priority",
        default="medium",
        tracking=True,
        help="Drives the SLA target. Kept separate from Odoo's two-value star priority.",
    )
    custom_story_points = fields.Integer(string="Story Points", default=0)
    custom_source = fields.Selection(TASK_SOURCES, string="Source", default="po", index=True)
    # Task dependencies are native since Odoo 17 (``depend_on_ids``, labelled "Blocked
    # By"), so there is no second blocker field here -- only the derived flag the board
    # and the API need.
    custom_is_blocked = fields.Boolean(compute="_compute_is_blocked", store=True)
    custom_due_sla_date = fields.Datetime(string="SLA Deadline")

    # ---------------------------------------------------------------- clock
    custom_stage_entered_at = fields.Datetime(
        string="Stage Entered At",
        readonly=True,
        help="When the task entered its current stage. Basis for booking elapsed time.",
    )
    custom_prev_stage_id = fields.Many2one(
        "project.task.type",
        string="Stage Before Hold",
        readonly=True,
        help="Where to send the task back to when the hold is released.",
    )
    custom_hold_reason = fields.Text(string="Hold Reason")
    custom_hold_by_id = fields.Many2one("res.users", string="Held By", readonly=True)
    custom_hold_since = fields.Datetime(string="On Hold Since", readonly=True)
    custom_hold_until = fields.Date(
        string="Hold Expected Until",
        help="Best estimate. Passing it raises a hold_expired notification -- a hold with "
        "no end date is how work disappears.",
    )
    custom_hold_duration_hours = fields.Float(
        string="Total Hold (hours)",
        readonly=True,
        digits=(10, 2),
    )
    custom_hold_expired_notified = fields.Boolean(readonly=True)

    custom_verification_owner_id = fields.Many2one(
        "res.partner",
        string="Verifying Brand PIC",
        help="Defaults to the vertical's brand PIC.",
    )
    custom_verification_requested_at = fields.Datetime(readonly=True)
    custom_verification_due = fields.Datetime(string="Verification Due", readonly=True)
    custom_user_wait_hours = fields.Float(
        string="Total User Wait (hours)",
        readonly=True,
        digits=(10, 2),
    )
    custom_verify_reminders_sent = fields.Integer(readonly=True, default=0)
    custom_auto_closed = fields.Boolean(string="Auto-closed", readonly=True)
    custom_closed_at = fields.Datetime(readonly=True)

    custom_cycle_time_team = fields.Float(
        string="Cycle Time — Team (hours)",
        compute="_compute_cycle_times",
        store=True,
        digits=(10, 2),
        help="Elapsed time minus hold minus user wait. What the team actually owned.",
    )
    custom_lead_time_total = fields.Float(
        string="Lead Time — Total (hours)",
        compute="_compute_cycle_times",
        store=True,
        digits=(10, 2),
        help="Plain elapsed time. What the requester experienced.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("depend_on_ids", "depend_on_ids.stage_id")
    def _compute_is_blocked(self):
        for task in self:
            task.custom_is_blocked = any(not blocker.stage_id.custom_is_closed_stage for blocker in task.depend_on_ids)

    @api.depends(
        "create_date",
        "custom_closed_at",
        "custom_hold_duration_hours",
        "custom_user_wait_hours",
    )
    def _compute_cycle_times(self):
        now = fields.Datetime.now()
        for task in self:
            start = task.create_date
            if not start:
                task.custom_cycle_time_team = 0.0
                task.custom_lead_time_total = 0.0
                continue
            end = task.custom_closed_at or now
            total = (end - start).total_seconds() / 3600.0
            task.custom_lead_time_total = round(max(total, 0.0), 2)
            team = total - (task.custom_hold_duration_hours or 0.0) - (task.custom_user_wait_hours or 0.0)
            task.custom_cycle_time_team = round(max(team, 0.0), 2)

    # ------------------------------------------------------------------
    # Working-day helpers (backed by resource.calendar global leaves)
    # ------------------------------------------------------------------

    @api.model
    def _pmo_holidays(self):
        leaves = self.env["resource.calendar.leaves"].search([("resource_id", "=", False)])
        days = set()
        for leave in leaves:
            day = fields.Datetime.to_datetime(leave.date_from).date()
            last = fields.Datetime.to_datetime(leave.date_to).date()
            while day <= last:
                days.add(day)
                day += timedelta(days=1)
        return days

    @api.model
    def _pmo_add_working_days(self, start, days):
        """Add ``days`` working days to ``start`` (datetime), skipping weekends and
        company-wide time off. Returns a datetime."""
        if days <= 0:
            return start
        holidays = self._pmo_holidays()
        cursor = start
        remaining = days
        while remaining > 0:
            cursor += timedelta(days=1)
            if cursor.weekday() >= 5 or cursor.date() in holidays:
                continue
            remaining -= 1
        return cursor

    # ------------------------------------------------------------------
    # Vertical inheritance
    # ------------------------------------------------------------------

    @api.model
    def _pmo_inherited_vertical(self, vals):
        """Vertical implied by the parent: change request first, then project.

        ``change_request_id`` only exists once ``custom_project_cr`` is installed, so the
        field is probed rather than assumed.
        """
        empty = self.env["custom.project.vertical"]
        cr_field = "change_request_id" in self._fields
        if cr_field and vals.get("change_request_id"):
            cr = self.env["custom.change.request"].browse(vals["change_request_id"])
            if cr.exists() and cr.vertical_id:
                return cr.vertical_id
        if vals.get("project_id"):
            return self.env["project.project"].browse(vals["project_id"]).custom_vertical_id
        return empty

    # ------------------------------------------------------------------
    # Stage clock
    # ------------------------------------------------------------------

    def _pmo_book_elapsed(self, now):
        """Book the time spent in the stage we are leaving into the right bucket."""
        for task in self:
            entered = task.custom_stage_entered_at or task.create_date
            if not entered:
                continue
            hours = (now - entered).total_seconds() / 3600.0
            if hours <= 0:
                continue
            clock = task.stage_id.custom_sla_clock
            if clock == "paused":
                task.custom_hold_duration_hours = (task.custom_hold_duration_hours or 0.0) + hours
            elif clock == "user_side":
                task.custom_user_wait_hours = (task.custom_user_wait_hours or 0.0) + hours

    def _pmo_check_transition(self, new_stage):
        for task in self:
            allowed = task.stage_id.custom_next_stage_ids
            if task.stage_id and allowed and new_stage not in allowed:
                raise UserError(
                    _(
                        "Moving %(task)s from %(from_stage)s straight to %(to_stage)s is not an "
                        "allowed transition. Allowed: %(allowed)s.",
                        task=task.name,
                        from_stage=task.stage_id.name,
                        to_stage=new_stage.name,
                        allowed=", ".join(allowed.mapped("name")) or "-",
                    )
                )

    def _pmo_enter_stage(self, new_stage, now, reason=None, previous=None):
        """Apply everything that entering ``new_stage`` implies.

        ``previous`` maps task id -> the stage left behind. It has to be passed in: by the
        time this runs, ``task.stage_id`` is already the NEW stage, so reading it here
        would record the hold stage as the stage to resume into.
        """
        previous = previous or {}
        for task in self:
            values = {"custom_stage_entered_at": now}
            if new_stage.custom_is_hold:
                if new_stage.custom_require_reason and not (reason or task.custom_hold_reason):
                    raise UserError(
                        _(
                            "Putting %s on hold needs a reason — a hold with no reason is how work goes quiet.",
                            task.name,
                        )
                    )
                came_from = previous.get(task.id)
                values.update(
                    {
                        "custom_prev_stage_id": came_from.id if came_from else False,
                        "custom_hold_since": now,
                        "custom_hold_by_id": self.env.user.id,
                        "custom_hold_expired_notified": False,
                    }
                )
                if reason:
                    values["custom_hold_reason"] = reason
            elif new_stage.custom_is_waiting_user:
                owner = task.custom_verification_owner_id
                if not owner:
                    owner = task.custom_vertical_id.pic_partner_ids[:1]
                due = self._pmo_add_working_days(now, new_stage.custom_auto_close_days or 5)
                values.update(
                    {
                        "custom_verification_requested_at": now,
                        "custom_verification_due": due,
                        "custom_verification_owner_id": owner.id if owner else False,
                        "custom_verify_reminders_sent": 0,
                    }
                )
            elif new_stage.custom_is_closed_stage:
                values["custom_closed_at"] = now
            task.write(values)

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("custom_stage_entered_at", fields.Datetime.now())
            if not vals.get("custom_vertical_id"):
                vertical = self._pmo_inherited_vertical(vals)
                if vertical:
                    vals["custom_vertical_id"] = vertical.id
            if not vals.get("custom_sprint_id"):
                vals["custom_sprint_id"] = self.env["custom.project.sprint"].current_sprint().id
        tasks = super().create(vals_list)
        for task in tasks:
            task._pmo_notify_event("task_created")
        return tasks

    def write(self, vals):
        now = fields.Datetime.now()
        new_stage = None
        if "stage_id" in vals and vals["stage_id"]:
            new_stage = self.env["project.task.type"].browse(vals["stage_id"])
            self._pmo_check_transition(new_stage)
            self._pmo_book_elapsed(now)

        stage_before = {task.id: task.stage_id for task in self}
        assignees_before = {task.id: set(task.user_ids.ids) for task in self}

        result = super().write(vals)

        if new_stage:
            reason = vals.get("custom_hold_reason")
            self._pmo_enter_stage(new_stage, now, reason=reason, previous=stage_before)
            for task in self:
                old = stage_before.get(task.id)
                task._pdp_audit_write(
                    "hold" if new_stage.custom_is_hold else "stage_change",
                    task.id,
                    {"stage_id": [old.name if old else None, new_stage.name]},
                    reason=reason or None,
                )
                if new_stage.custom_is_hold:
                    task._pmo_notify_event("on_hold")
                elif new_stage.custom_is_waiting_user:
                    task._pmo_notify_event("verify_request")
                elif new_stage.custom_is_closed_stage:
                    task._pmo_notify_event("task_closed")
                else:
                    task._pmo_notify_event("stage_changed")

        if "user_ids" in vals:
            for task in self:
                if set(task.user_ids.ids) != assignees_before.get(task.id, set()):
                    task._pdp_audit_write("assign", task.id, {"user_ids": sorted(task.user_ids.ids)})
                    task._pmo_notify_event("assigned")

        return result

    # ------------------------------------------------------------------
    # Notification hook -- overridden by custom_project_notify
    # ------------------------------------------------------------------

    def _pmo_notify_event(self, event, extra=None):
        """No-op hook. ``custom_project_notify`` overrides it to queue an outbox row.

        Kept here so this module stays installable and testable on its own.
        """
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_pmo_hold(self):
        """Open the hold wizard-less flow: requires custom_hold_reason to be set."""
        self.ensure_one()
        stage = self.env["project.task.type"]._stage_by_code("hold")
        if not stage:
            raise UserError(_("No stage is flagged as Hold. Configure one in Settings."))
        if not self.custom_hold_reason:
            raise UserError(_("Fill in the hold reason first."))
        self.write({"stage_id": stage.id})
        return True

    def action_pmo_resume(self):
        self.ensure_one()
        if not self.stage_id.custom_is_hold:
            raise UserError(_("This task is not on hold."))
        target = self.custom_prev_stage_id or self.env["project.task.type"]._stage_by_code("dev")
        if not target:
            raise UserError(_("Cannot tell which stage to resume into."))
        self.write({"stage_id": target.id})
        self._pdp_audit_write("resume", self.id, {"stage_id": target.name})
        self._pmo_notify_event("resumed")
        return True

    def action_pmo_request_verification(self):
        self.ensure_one()
        stage = self.env["project.task.type"]._stage_by_code("waiting_user")
        if not stage:
            raise UserError(_("No stage is flagged as Waiting User Verification. Configure one in Settings."))
        self.write({"stage_id": stage.id})
        return True

    def action_pmo_verified(self):
        self.ensure_one()
        stage = self.env["project.task.type"]._stage_by_code("done")
        if not stage:
            raise UserError(_("No closing stage is configured."))
        self.write({"stage_id": stage.id})
        self._pdp_audit_write("verify_done", self.id, None)
        return True

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------

    @api.model
    def cron_pmo_verification(self):
        """Remind the brand, then auto-close silence.

        Runs hourly. Reminders are counted on the record, so a task is nudged twice and
        then closed -- never re-nudged every hour.
        """
        now = fields.Datetime.now()
        waiting = self.search(
            [
                ("stage_id.custom_is_waiting_user", "=", True),
                ("custom_verification_due", "!=", False),
            ]
        )
        done_stage = self.env["project.task.type"]._stage_by_code("done")
        for task in waiting:
            requested = task.custom_verification_requested_at or now
            elapsed_days = (now - requested).days
            if now >= task.custom_verification_due:
                if not done_stage:
                    _logger.warning("VAS PMO: no closing stage, cannot auto-close %s", task.id)
                    continue
                task.write({"stage_id": done_stage.id, "custom_auto_closed": True})
                task._pdp_audit_write(
                    "auto_close",
                    task.id,
                    None,
                    reason=_("No verification within the agreed window"),
                )
                task._pmo_notify_event("verify_auto_close")
            elif elapsed_days >= 5 and task.custom_verify_reminders_sent < 2:
                task.custom_verify_reminders_sent = 2
                task._pmo_notify_event("verify_reminder_h5")
            elif elapsed_days >= 2 and task.custom_verify_reminders_sent < 1:
                task.custom_verify_reminders_sent = 1
                task._pmo_notify_event("verify_reminder_h2")

    @api.model
    def cron_pmo_hold_watch(self):
        """Flag holds that outlived their own estimate."""
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("stage_id.custom_is_hold", "=", True),
                ("custom_hold_until", "!=", False),
                ("custom_hold_until", "<", today),
                ("custom_hold_expired_notified", "=", False),
            ]
        )
        for task in stale:
            task.custom_hold_expired_notified = True
            task._pmo_notify_event("hold_expired")

    @api.model
    def cron_pmo_sla(self):
        """H-3 / H-1 / overdue / escalation, but only while the clock is running."""
        now = fields.Datetime.now()
        candidates = self.search(
            [
                ("custom_due_sla_date", "!=", False),
                ("stage_id.custom_sla_clock", "=", "running"),
            ]
        )
        for task in candidates:
            remaining_days = (task.custom_due_sla_date - now).days
            if remaining_days < 0:
                overdue_days = abs(remaining_days)
                event = "escalation" if overdue_days >= 3 else "overdue"
            elif remaining_days <= 1:
                event = "due_h1"
            elif remaining_days <= 3:
                event = "due_h3"
            else:
                continue
            task._pmo_notify_event(event)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("custom_vertical_override", "custom_vertical_override_reason")
    def _check_override_reason(self):
        for task in self:
            if task.custom_vertical_override and not task.custom_vertical_override_reason:
                raise ValidationError(_("Overriding the vertical of %s needs a reason.", task.name))
