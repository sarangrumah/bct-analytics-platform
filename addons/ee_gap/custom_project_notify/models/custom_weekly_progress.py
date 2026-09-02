# -*- coding: utf-8 -*-
"""Weekly reminders and the Monday digest.

``custom.weekly.progress`` has no chatter, so the Odoo channel degrades to nothing here --
the mixin checks for ``message_post`` before using it. WhatsApp and e-mail are the point
for these events anyway: a reminder that only appears inside the app is not a reminder.
"""

from odoo import models


class CustomWeeklyProgress(models.Model):
    _name = "custom.weekly.progress"
    _inherit = ["custom.weekly.progress", "pmo.notify.source"]

    def _pmo_recipient_map(self):
        self.ensure_one()
        project = self.project_id
        return {
            "assignee": self.author_id,
            "ba": self.author_id,
            "po": project.custom_po_id if project else self.env["res.users"],
            "portfolio_owner": (project.custom_portfolio_id.owner_id if project else self.env["res.users"]),
            "vertical_owner": (self.vertical_id.vertical_po_id if self.vertical_id else self.env["res.users"]),
        }

    def _pmo_event_context(self, event):
        self.ensure_one()
        return {
            "week": self.week_code,
            "project": self.project_id.display_name or "",
            "done_count": self.done_count,
            "done_points": self.done_points,
            "carry_over": self.carry_over_count,
            "hours": self.hours_spent,
            "cycle_time_team": self.cycle_time_team,
            "lead_time_total": self.lead_time_total,
            "hold_count": self.hold_count,
            "waiting_user_count": self.waiting_user_count,
            "blocker": (self.blocker or "")[:500],
            "state": self.state,
        }

    def _pmo_notify_weekly_event(self, event, extra=None):
        for record in self:
            record._pmo_dispatch(event, extra=extra)
        return True
