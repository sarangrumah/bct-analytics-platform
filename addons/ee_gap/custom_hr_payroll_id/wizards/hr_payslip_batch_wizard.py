# -*- coding: utf-8 -*-
"""Generate + compute payslips for a batch of employees in one period."""

from __future__ import annotations

import logging
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrPayslipBatchWizard(models.TransientModel):
    _name = "hr.payslip.batch.wizard"
    _description = "Wizard: run payroll for a batch of employees"

    period_year = fields.Integer(required=True, default=lambda s: date.today().year)
    period_month = fields.Selection(
        [(str(i), f"{i:02d}") for i in range(1, 13)],
        required=True,
        default=lambda s: str(date.today().month),
    )
    is_thr = fields.Boolean(string="THR Run")
    employee_ids = fields.Many2many(
        "hr.employee",
        string="Employees",
        help="Empty = all active employees in the current company.",
    )
    auto_approve = fields.Boolean(default=False)
    skip_if_exists = fields.Boolean(
        default=True,
        help="If a payslip already exists for an employee + period, skip rather than fail.",
    )

    # Result fields
    run_done = fields.Boolean(readonly=True)
    payslip_ids = fields.Many2many("hr.payslip", string="Generated", readonly=True)
    summary = fields.Html(readonly=True)

    def action_run(self):
        self.ensure_one()
        Employee = self.env["hr.employee"].sudo()
        Payslip = self.env["hr.payslip"].sudo()

        employees = self.employee_ids or Employee.search(
            [
                ("active", "=", True),
                ("company_id", "=", self.env.company.id),
            ]
        )
        if not employees:
            raise UserError(_("No employees in scope."))

        created = self.env["hr.payslip"]
        skipped = 0
        errors: list[str] = []

        for emp in employees:
            domain = [
                ("employee_id", "=", emp.id),
                ("period_year", "=", self.period_year),
                ("period_month", "=", self.period_month),
                ("is_thr", "=", self.is_thr),
            ]
            if Payslip.search(domain, limit=1):
                if self.skip_if_exists:
                    skipped += 1
                    continue
                raise UserError(
                    _("Payslip already exists for %s in %s-%s. Tick 'Skip if exists' to ignore.")
                    % (emp.name, self.period_year, self.period_month)
                )

            try:
                # Take gross_salary from employee record (operator pre-fills this on
                # hr.employee; for production setups this comes from contract).
                slip = Payslip.create(
                    {
                        "employee_id": emp.id,
                        "period_year": self.period_year,
                        "period_month": self.period_month,
                        "is_thr": self.is_thr,
                        "gross_salary": getattr(emp, "x_custom_gaji_pokok", 0.0) or 0.0,
                    }
                )
                slip.action_compute()
                if self.auto_approve:
                    slip.action_approve()
                created |= slip
            except Exception as e:
                _logger.exception("Batch payroll: failed for employee %s", emp.id)
                errors.append(f"{emp.name}: {e}")

        # Summary HTML
        parts = [
            f"<p><b>{len(created)}</b> payslips generated.</p>",
            f"<p>{skipped} skipped (already existed).</p>",
        ]
        if errors:
            parts.append(
                f'<div class="alert alert-danger"><b>{len(errors)} errors:</b><ul>'
                + "".join(f"<li>{e}</li>" for e in errors)
                + "</ul></div>"
            )
        self.write(
            {
                "run_done": True,
                "payslip_ids": [(6, 0, created.ids)],
                "summary": "".join(parts),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_generated(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.payslip",
            "view_mode": "list,form",
            "domain": [("id", "in", self.payslip_ids.ids)],
        }
