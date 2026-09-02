# -*- coding: utf-8 -*-
"""Auto-create OOO record from approved hr.leave entries."""

from __future__ import annotations

from odoo import _, fields, models


class HrLeave(models.Model):
    _inherit = "hr.leave"

    x_custom_ooo_id = fields.Many2one("approval.ooo", string="OOO Record", copy=False)

    def action_approve(self, *args, **kwargs):  # signature differs across Odoo versions
        res = super().action_approve(*args, **kwargs)
        for leave in self:
            leave._create_or_update_ooo()
        return res

    # Some Odoo 19 versions use action_validate as the final approval step
    def action_validate(self):
        res = super().action_validate()
        for leave in self:
            leave._create_or_update_ooo()
        return res

    def action_refuse(self):
        res = super().action_refuse()
        for leave in self:
            if leave.x_custom_ooo_id:
                leave.x_custom_ooo_id.sudo().write({"active": False})
        return res

    def _create_or_update_ooo(self):
        self.ensure_one()
        if self.state not in ("validate", "validate1"):
            return
        if not self.employee_id or not self.employee_id.user_id:
            return
        user = self.employee_id.user_id
        manager = self.employee_id.parent_id.user_id if self.employee_id.parent_id else False
        OOO = self.env["approval.ooo"].sudo()
        vals = {
            "user_id": user.id,
            "leave_id": self.id,
            "date_from": fields.Datetime.to_datetime(self.date_from),
            "date_to": fields.Datetime.to_datetime(self.date_to),
            "auto_delegate_to_id": manager.id if manager else False,
            "note": _("Auto-created from leave %s") % (self.holiday_status_id.name or self.id),
            "active": True,
        }
        if self.x_custom_ooo_id:
            self.x_custom_ooo_id.write(vals)
        else:
            ooo = OOO.create(vals)
            self.x_custom_ooo_id = ooo.id
