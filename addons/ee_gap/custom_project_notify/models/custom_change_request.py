# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomChangeRequest(models.Model):
    _name = "custom.change.request"
    _inherit = ["custom.change.request", "pmo.notify.source"]

    def _pmo_recipient_map(self):
        self.ensure_one()
        pending = self.approval_ids.filtered(lambda a: a.state == "pending")[:1]
        return {
            "assignee": pending.user_id if pending else self.ba_id,
            "reporter": self.requester_partner_id,
            "ba": self.ba_id,
            "po": self.po_id,
            "vertical_owner": self.vertical_id.vertical_po_id,
            "portfolio_owner": self.project_id.custom_portfolio_id.owner_id,
            "brand_pic": self.vertical_id.pic_partner_ids,
        }

    def _pmo_event_context(self, event):
        self.ensure_one()
        return {
            "cr_code": self.code,
            "cr_type": self.cr_type,
            "impact": self.impact,
            "priority": self.priority,
            "stage": self.stage_id.name,
            "approval_state": self.approval_state,
            "approval_progress": self.approval_progress,
            "effort_days": self.effort_estimate_days,
            "need_downtime": self.need_downtime,
            "reject_reason": self.reject_reason or "",
            "sla_response_due": (fields.Datetime.to_string(self.sla_response_due) if self.sla_response_due else ""),
            "project": self.project_id.display_name or "",
        }

    def _cr_notify_event(self, event, extra=None):
        for record in self:
            record._pmo_dispatch(event, extra=extra)
        return True
