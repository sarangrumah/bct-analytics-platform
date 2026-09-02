# -*- coding: utf-8 -*-
import logging
from datetime import date

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    x_auto_leave_allocation = fields.Boolean(
        string="Auto Annual Leave Allocation",
        default=True,
        help="If set, automatically create a pro-rated Cuti Tahunan allocation "
        "for this employee on creation (based on hire date / current year).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        for employee in employees:
            if employee.x_auto_leave_allocation:
                try:
                    employee._x_create_initial_annual_allocations()
                except Exception:
                    _logger.exception(
                        "custom_hr_leave_id: failed to auto-create annual allocation for employee id=%s",
                        employee.id,
                    )
        return employees

    def _x_create_initial_annual_allocations(self):
        """Create pro-rated annual-leave (Cuti Tahunan) allocations.

        For each leave type whose `x_id_leave_category == 'cuti_tahunan'`, allocate
        12 days pro-rated from hire_date (or today if no hire date) to year end.
        Skips when an allocation already exists for this employee/type in the
        current year.
        """
        self.ensure_one()
        Allocation = self.env["hr.leave.allocation"].sudo()
        LeaveType = self.env["hr.leave.type"].sudo()

        today = fields.Date.context_today(self)
        ref_date = self.create_date.date() if self.create_date else today
        hire = getattr(self, "first_contract_date", False) or ref_date
        if isinstance(hire, str):
            hire = fields.Date.to_date(hire)
        if not hire:
            hire = today

        year_start = date(today.year, 1, 1)
        year_end = date(today.year, 12, 31)
        start = max(hire, year_start)
        if start > year_end:
            return

        remaining_days = (year_end - start).days + 1
        total_days = (year_end - year_start).days + 1
        # Pro-rate 12 annual leave days based on remaining year fraction.
        prorated = max(0, round(12.0 * remaining_days / total_days))

        annual_types = LeaveType.search([("x_id_leave_category", "=", "cuti_tahunan")])
        for ltype in annual_types:
            existing = Allocation.search(
                [
                    ("employee_id", "=", self.id),
                    ("holiday_status_id", "=", ltype.id),
                    ("date_from", ">=", year_start),
                    ("date_from", "<=", year_end),
                ],
                limit=1,
            )
            if existing:
                continue
            allocation_vals = {
                "name": _("Annual Leave %s (auto-allocated)") % today.year,
                "employee_id": self.id,
                "holiday_status_id": ltype.id,
                "number_of_days": prorated,
                "date_from": start,
                "date_to": year_end,
            }
            # allocation_type field exists in hr_holidays; default 'regular'
            if "allocation_type" in Allocation._fields:
                allocation_vals["allocation_type"] = "regular"
            Allocation.create(allocation_vals)
        return True
