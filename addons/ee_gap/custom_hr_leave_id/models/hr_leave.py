# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class HrLeave(models.Model):
    _name = "hr.leave"
    _inherit = ["hr.leave", "approval.mixin"]

    x_id_leave_category = fields.Selection(
        related="holiday_status_id.x_id_leave_category",
        store=True,
        readonly=True,
        string="ID Leave Category",
    )

    x_overlapping_holidays = fields.Many2many(
        "id.public.holiday",
        string="Overlapping Public Holidays",
        compute="_compute_x_overlapping_holidays",
        store=False,
        help="Indonesian public holidays that fall within the leave period. "
        "These days are typically excluded from leave-day counting.",
    )

    x_overlapping_holidays_count = fields.Integer(
        string="Overlapping Holidays Count",
        compute="_compute_x_overlapping_holidays",
        store=False,
    )

    x_overlapping_holidays_warning = fields.Char(
        string="Holiday Overlap Warning",
        compute="_compute_x_overlapping_holidays",
        store=False,
    )

    @api.depends("date_from", "date_to")
    def _compute_x_overlapping_holidays(self):
        Holiday = self.env["id.public.holiday"]
        for rec in self:
            holidays = Holiday.browse()
            if rec.date_from and rec.date_to:
                d_from = fields.Date.to_date(rec.date_from)
                d_to = fields.Date.to_date(rec.date_to)
                if d_from and d_to and d_from <= d_to:
                    holidays = Holiday.search(
                        [
                            ("date", ">=", d_from),
                            ("date", "<=", d_to),
                        ]
                    )
            rec.x_overlapping_holidays = [(6, 0, holidays.ids)]
            rec.x_overlapping_holidays_count = len(holidays)
            if holidays:
                rec.x_overlapping_holidays_warning = _(
                    "%s public holiday(s) overlap with this leave period and are normally not counted as leave days."
                ) % len(holidays)
            else:
                rec.x_overlapping_holidays_warning = False

    # ------------------------------------------------------------------
    # Approval engine integration
    # ------------------------------------------------------------------
    # Gate the employee submit (Confirm Request) on the approval engine.
    # When a leave matrix matches, clicking Confirm auto-submits the approval
    # request and leaves the request in Waiting Approval; only leaves needing
    # no approval (no matrix) or already approved proceed into Odoo's native
    # manager-approval queue. After the final tier approves, the engine
    # re-runs action_confirm via _approval_on_granted. Leaves are opt-in: with
    # no configured matrix the engine is a no-op and native behaviour stands.
    def action_confirm(self):
        proceed = self.browse()
        for leave in self:
            if leave._approval_request_or_proceed():
                proceed |= leave
        if proceed:
            return super(HrLeave, proceed).action_confirm()
        return True

    def _approval_on_granted(self):
        return self.action_confirm()
