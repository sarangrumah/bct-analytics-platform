# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


_RECURRENCE_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}


class CustomTodoTemplate(models.Model):
    _name = "custom.todo.template"
    _description = "Custom Todo Template"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    description = fields.Html()
    default_user_id = fields.Many2one("res.users", string="Default Assignee")
    default_project_id = fields.Many2one("project.project", string="Default Project")
    recurrence_rule = fields.Selection(
        [
            ("none", "None"),
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
        default="none",
        required=True,
        tracking=True,
    )
    is_active = fields.Boolean(default=True, tracking=True)
    last_created_at = fields.Datetime(
        string="Last Instantiated At",
        readonly=True,
        help="Timestamp of the most recent task created from this template.",
    )

    def action_create_task(self):
        Task = self.env["project.task"]
        tasks = self.env["project.task"]
        now = fields.Datetime.now()
        for tmpl in self:
            vals = {
                "name": tmpl.name,
                "description": tmpl.description or False,
            }
            if tmpl.default_user_id:
                vals["user_ids"] = [(6, 0, [tmpl.default_user_id.id])]
            if tmpl.default_project_id:
                vals["project_id"] = tmpl.default_project_id.id
            tasks |= Task.create(vals)
            tmpl.last_created_at = now
        if len(tasks) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "project.task",
                "res_id": tasks.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    @api.model
    def cron_create_recurring_todos(self):
        """Cron: instantiate tasks for templates whose recurrence is due.

        A template is due when:
          * ``is_active`` is True
          * ``recurrence_rule`` is one of daily/weekly/monthly
          * ``last_created_at`` is empty OR is older than the recurrence
            window (daily=1d, weekly=7d, monthly=30d).
        """
        now = fields.Datetime.now()
        templates = self.search(
            [
                ("is_active", "=", True),
                ("recurrence_rule", "!=", "none"),
            ]
        )
        created = 0
        for tmpl in templates:
            window_days = _RECURRENCE_DAYS.get(tmpl.recurrence_rule)
            if not window_days:
                continue
            if tmpl.last_created_at:
                next_due = tmpl.last_created_at + timedelta(days=window_days)
                if next_due > now:
                    continue
            try:
                tmpl.action_create_task()
                created += 1
            except Exception as e:  # pragma: no cover - defensive
                _logger.warning(
                    "cron_create_recurring_todos: template %s failed: %s",
                    tmpl.name,
                    e,
                )
        _logger.info(
            "cron_create_recurring_todos: instantiated %d task(s) from %d active template(s)",
            created,
            len(templates),
        )
        return True
