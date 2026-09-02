# -*- coding: utf-8 -*-
"""Wire the task's no-op notification hook to the real dispatcher."""

from odoo import fields, models


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "pmo.notify.source"]

    def _pmo_recipient_map(self):
        self.ensure_one()
        vertical = self.custom_vertical_id
        project = self.project_id
        reporter = self.env["res.users"]
        if "change_request_id" in self._fields and self.change_request_id:
            reporter = self.change_request_id.ba_id
        return {
            "assignee": self.user_ids,
            "reporter": reporter or self.create_uid,
            "ba": project.custom_ba_id if project else self.env["res.users"],
            "po": project.custom_po_id if project else self.env["res.users"],
            "portfolio_owner": (project.custom_portfolio_id.owner_id if project else self.env["res.users"]),
            "vertical_owner": vertical.vertical_po_id if vertical else self.env["res.users"],
            "brand_pic": (
                self.custom_verification_owner_id or (vertical.pic_partner_ids if vertical else self.env["res.partner"])
            ),
        }

    def _pmo_event_context(self, event):
        self.ensure_one()
        return {
            "stage": self.stage_id.name,
            "stage_code": self.stage_id.custom_code,
            "priority": self.custom_priority,
            "project": self.project_id.display_name,
            "sprint": self.custom_sprint_id.week_code,
            "cr_code": self.custom_cr_code if "custom_cr_code" in self._fields else False,
            "deadline": fields.Date.to_string(self.date_deadline) if self.date_deadline else "",
            "sla_due": (fields.Datetime.to_string(self.custom_due_sla_date) if self.custom_due_sla_date else ""),
            "hold_reason": self.custom_hold_reason or "",
            "hold_until": (fields.Date.to_string(self.custom_hold_until) if self.custom_hold_until else ""),
            "verification_due": (
                fields.Datetime.to_string(self.custom_verification_due) if self.custom_verification_due else ""
            ),
            "auto_close_days": self.stage_id.custom_auto_close_days,
            "cycle_time_team": self.custom_cycle_time_team,
            "lead_time_total": self.custom_lead_time_total,
        }

    def _pmo_notify_event(self, event, extra=None):
        for task in self:
            task._pmo_dispatch(event, extra=extra)
        return True
