# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields, models

_logger = logging.getLogger(__name__)


class LunchOrder(models.Model):
    _inherit = "lunch.order"

    x_payroll_deduction = fields.Boolean(
        string="Deduct from payroll",
        default=True,
    )
    x_payslip_id = fields.Many2one(
        "hr.payslip",
        string="Payslip",
        readonly=True,
    )

    def cron_aggregate_lunch_to_payroll(self):
        """Aggregate confirmed lunch orders per employee for the previous month.

        Stub implementation: logs the per-employee totals only.
        TODO: create matching hr.payslip.line entries (one per employee) and
        link them back via x_payslip_id once the payroll rule structure is
        finalised.
        """
        today = date.today()
        period_end = today.replace(day=1) - relativedelta(days=1)
        period_start = period_end.replace(day=1)

        domain = [
            ("x_payroll_deduction", "=", True),
            ("x_payslip_id", "=", False),
            ("date", ">=", period_start),
            ("date", "<=", period_end),
            ("state", "in", ["confirmed", "ordered"]),
        ]
        orders = self.search(domain)
        totals = defaultdict(float)
        for order in orders:
            user = order.user_id
            employee = user.employee_id if user else False
            if not employee:
                continue
            totals[employee.id] += order.price or 0.0

        employee_model = self.env["hr.employee"]
        for employee_id, total in totals.items():
            employee = employee_model.browse(employee_id)
            _logger.info(
                "[custom_lunch] Payroll aggregation %s..%s: employee=%s (id=%s) total=%.2f",
                period_start,
                period_end,
                employee.name,
                employee.id,
                total,
            )
        _logger.info(
            "[custom_lunch] cron_aggregate_lunch_to_payroll processed %s orders for %s employees",
            len(orders),
            len(totals),
        )
        return True
