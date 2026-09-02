# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ProjectTask(models.Model):
    _inherit = "project.task"

    x_pomodoro_state = fields.Selection(
        [
            ("idle", "Idle"),
            ("focus", "Focus"),
            ("break", "Break"),
            ("done", "Done"),
        ],
        string="Pomodoro State",
        default="idle",
    )
    x_pomodoro_minutes_focus = fields.Integer(
        string="Focus Minutes",
        default=25,
    )
    x_pomodoro_minutes_break = fields.Integer(
        string="Break Minutes",
        default=5,
    )
    x_pomodoro_started_at = fields.Datetime(string="Pomodoro Started At")
    x_ai_breakdown = fields.Text(
        string="AI Breakdown",
        help="AI-generated subtask suggestions",
    )

    # ---------- pomodoro buttons ----------

    def action_pomodoro_start_focus(self):
        self.write(
            {
                "x_pomodoro_state": "focus",
                "x_pomodoro_started_at": fields.Datetime.now(),
            }
        )
        return True

    def action_pomodoro_start_break(self):
        self.write(
            {
                "x_pomodoro_state": "break",
                "x_pomodoro_started_at": fields.Datetime.now(),
            }
        )
        return True

    def action_pomodoro_done(self):
        self.write({"x_pomodoro_state": "done"})
        return True

    def action_pomodoro_tick(self):
        """Called periodically (e.g. every minute from JS or cron).

        For tasks currently in 'focus' state: if elapsed since
        x_pomodoro_started_at exceeds x_pomodoro_minutes_focus, automatically
        transition to 'break' and reset the start timestamp.

        For tasks in 'break' state: if elapsed exceeds
        x_pomodoro_minutes_break, transition back to 'idle' (cycle complete).

        Returns a list of dicts describing the transitions performed (handy
        for JS callers and for tests).
        """
        now = fields.Datetime.now()
        transitions = []
        for task in self:
            if not task.x_pomodoro_started_at:
                continue
            elapsed = (now - task.x_pomodoro_started_at).total_seconds() / 60.0
            if task.x_pomodoro_state == "focus":
                if elapsed >= max(task.x_pomodoro_minutes_focus or 25, 1):
                    task.write(
                        {
                            "x_pomodoro_state": "break",
                            "x_pomodoro_started_at": now,
                        }
                    )
                    transitions.append(
                        {
                            "task_id": task.id,
                            "from": "focus",
                            "to": "break",
                            "elapsed_minutes": elapsed,
                        }
                    )
                    task.message_post(
                        body=_("Pomodoro: focus complete, switched to break."),
                        subtype_xmlid="mail.mt_note",
                    )
            elif task.x_pomodoro_state == "break":
                if elapsed >= max(task.x_pomodoro_minutes_break or 5, 1):
                    task.write(
                        {
                            "x_pomodoro_state": "idle",
                            "x_pomodoro_started_at": False,
                        }
                    )
                    transitions.append(
                        {
                            "task_id": task.id,
                            "from": "break",
                            "to": "idle",
                            "elapsed_minutes": elapsed,
                        }
                    )
                    task.message_post(
                        body=_("Pomodoro: break complete, cycle finished."),
                        subtype_xmlid="mail.mt_note",
                    )
        return transitions

    @api.model
    def cron_pomodoro_tick(self):
        """Cron driver for pomodoro auto-transition.

        Picks all tasks with an active pomodoro state and calls tick on them.
        """
        active = self.search(
            [
                ("x_pomodoro_state", "in", ("focus", "break")),
                ("x_pomodoro_started_at", "!=", False),
            ]
        )
        if active:
            active.action_pomodoro_tick()
        return True

    # ---------- AI breakdown ----------

    def _custom_ai_breakdown_payload(self):
        self.ensure_one()
        return {
            "name": self.name or "",
            "description": (self.description or "")[:4000],
        }

    @staticmethod
    def _parse_ai_subtasks(result):
        """Extract a list of subtask name strings from a recommend response.

        Tolerant of shape: looks for a ``subtasks`` key with a list value where
        each entry is either a string or a dict with a ``text``/``name``/
        ``title`` key. Returns ``[]`` if none found.
        """
        if not isinstance(result, dict):
            return []
        raw = result.get("subtasks")
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = (item.get("text") or item.get("name") or item.get("title") or "").strip()
            else:
                continue
            if text:
                out.append(text[:255])
        return out

    def action_ai_breakdown(self):
        self.ensure_one()
        try:
            result = self.env["custom.ai"]._recommend(
                model="project.task",
                res_id=self.id,
                payload=self._custom_ai_breakdown_payload(),
            )
        except Exception as e:
            _logger.error("AI breakdown failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = (
            result.get("breakdown")
            or result.get("response")
            or result.get("text")
            or result.get("summary")
            or json.dumps(result)[:1000]
        )
        self.x_ai_breakdown = text

        # Parse and create real child tasks if subtasks were returned
        subtask_names = self._parse_ai_subtasks(result)
        created = self.env["project.task"]
        if subtask_names:
            assignee_id = self.user_ids[:1].id if self.user_ids else False
            for st_name in subtask_names:
                vals = {
                    "name": st_name,
                    "parent_id": self.id,
                }
                if self.project_id:
                    vals["project_id"] = self.project_id.id
                if assignee_id:
                    vals["user_ids"] = [(6, 0, [assignee_id])]
                created |= self.env["project.task"].create(vals)

        body = _("<b>AI Task Breakdown</b><br/>%s") % text
        if created:
            body += _("<br/><br/>Created %d subtask(s).") % len(created)
        self.message_post(
            body=body,
            author_id=self.env.ref("base.partner_root").id,
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ---------- standup digest helpers ----------

    @api.model
    def _standup_user_summary(self, user, since_dt, until_dt):
        """Return ``(done_yesterday, in_progress_today)`` recordsets for user."""
        # Tasks the user closed since `since_dt`
        done = self.search(
            [
                ("user_ids", "in", user.id),
                ("state", "in", ("1_done", "1_canceled")),
                ("write_date", ">=", since_dt),
                ("write_date", "<", until_dt),
            ]
        )
        # Tasks still open assigned to user (in-progress / planned)
        in_progress = self.search(
            [
                ("user_ids", "in", user.id),
                ("state", "in", ("01_in_progress", "02_changes_requested", "03_approved")),
            ]
        )
        return done, in_progress

    @api.model
    def cron_send_daily_standup(self):
        """Cron: send each active internal user their daily standup digest.

        Done yesterday + In-progress today, scoped by assigned user.
        """
        template = self.env.ref(
            "custom_todo.mail_template_daily_standup",
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("custom_todo: standup template missing, skipping cron")
            return False
        Users = self.env["res.users"]
        users = Users.search(
            [
                ("active", "=", True),
                ("share", "=", False),
            ]
        )
        today = fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        sent = 0
        for user in users:
            done, in_progress = self._standup_user_summary(user, yesterday, today)
            if not done and not in_progress:
                continue
            ctx = {
                "user": user,
                "done_tasks": done,
                "in_progress_tasks": in_progress,
                "digest_date": today.date(),
            }
            try:
                template.with_context(**ctx).send_mail(
                    user.id,
                    force_send=False,
                    email_values={"email_to": user.email},
                )
                sent += 1
            except Exception as e:  # pragma: no cover - defensive
                _logger.warning("standup digest failed for %s: %s", user.login, e)
        _logger.info("custom_todo: standup digest sent to %d users", sent)
        return True
