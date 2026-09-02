# -*- coding: utf-8 -*-
from odoo import models


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "pmo.notify.source"]

    def _pmo_recipient_map(self):
        self.ensure_one()
        return {
            "po": self.custom_po_id,
            "ba": self.custom_ba_id,
            "portfolio_owner": self.custom_portfolio_id.owner_id,
            "vertical_owner": self.custom_vertical_id.vertical_po_id,
            "brand_pic": self.custom_vertical_id.pic_partner_ids,
        }

    def _pmo_event_context(self, event):
        self.ensure_one()
        return {
            "health": self.custom_health,
            "health_note": self.custom_health_note or "",
            "progress": self.custom_progress,
            "overdue": self.custom_task_overdue_count,
            "hold": self.custom_task_hold_count,
            "waiting_user": self.custom_task_waiting_user_count,
        }

    def _pmo_notify_project_event(self, event, extra=None):
        for project in self:
            project._pmo_dispatch(event, extra=extra)
        return True
