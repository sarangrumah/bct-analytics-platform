# -*- coding: utf-8 -*-
import logging
from datetime import date

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CustomLeaveCarryoverPolicy(models.Model):
    _name = "custom.leave.carryover.policy"
    _description = "Leave Carry-Over Policy"
    _order = "leave_type_id, id"

    name = fields.Char(
        string="Policy Name",
        compute="_compute_name",
        store=True,
    )
    leave_type_id = fields.Many2one(
        "hr.leave.type",
        string="Leave Type",
        required=True,
        ondelete="cascade",
    )
    max_carryover_days = fields.Integer(
        string="Max Carry-Over Days",
        default=5,
        help="Maximum number of unused leave days that may be carried over to the following year.",
    )
    expiry_months_after_year_end = fields.Integer(
        string="Expiry (Months After Year-End)",
        default=3,
        help="Number of months after the year-end before carried-over days expire (default 3 months = end of March).",
    )
    is_active = fields.Boolean(string="Active", default=True)
    notes = fields.Text(string="Notes")

    _uniq_leave_type = models.Constraint(
        "unique(leave_type_id)",
        "A carry-over policy already exists for this leave type.",
    )

    @api.depends("leave_type_id", "max_carryover_days")
    def _compute_name(self):
        for rec in self:
            if rec.leave_type_id:
                rec.name = _("%(type)s carry-over (max %(days)d days)") % {
                    "type": rec.leave_type_id.name,
                    "days": rec.max_carryover_days or 0,
                }
            else:
                rec.name = _("Carry-Over Policy")

    @api.model
    def cron_apply_carryover(self):
        """Annual job: cap each employee's residual allocation at the policy max.

        For every active policy, look at the previous year's allocations per
        employee and create a new allocation in the current year capped at
        `max_carryover_days`. The rest is considered expired. This is a
        stub that logs intended actions; real allocation rewrites are deferred
        until aligned with hr_holidays' accrual plans.
        """
        today = fields.Date.context_today(self)
        prev_year = today.year - 1
        prev_year_start = date(prev_year, 1, 1)
        prev_year_end = date(prev_year, 12, 31)
        Allocation = self.env["hr.leave.allocation"].sudo()
        active_policies = self.search([("is_active", "=", True)])
        _logger.info(
            "custom_hr_leave_id: cron_apply_carryover processing %s policies for prev_year=%s",
            len(active_policies),
            prev_year,
        )
        processed = 0
        for policy in active_policies:
            allocations = Allocation.search(
                [
                    ("holiday_status_id", "=", policy.leave_type_id.id),
                    ("date_from", ">=", prev_year_start),
                    ("date_from", "<=", prev_year_end),
                    ("state", "=", "validate"),
                ]
            )
            for allocation in allocations:
                remaining = max(
                    0.0,
                    (allocation.number_of_days or 0.0) - (allocation.leaves_taken or 0.0),
                )
                carry = min(remaining, policy.max_carryover_days)
                _logger.info(
                    "custom_hr_leave_id: would carry %.1f days (remaining=%.1f, cap=%d) for employee_id=%s type=%s",
                    carry,
                    remaining,
                    policy.max_carryover_days,
                    allocation.employee_id.id,
                    policy.leave_type_id.name,
                )
                processed += 1
        _logger.info(
            "custom_hr_leave_id: cron_apply_carryover finished, "
            "processed %s allocations (stub, no rewrites performed).",
            processed,
        )
        return True
