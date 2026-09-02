# -*- coding: utf-8 -*-
"""Per-employee analytic account so a single petty-cash account can be sliced
by employee (a dedicated "Employee" analytic plan, separate from the
"Operating Unit" plan)."""

from __future__ import annotations

from odoo import fields, models

PC_EMPLOYEE_PLAN = "Employee"


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    pc_analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Petty Cash Analytic",
        copy=False,
        help="Analytic account (plan 'Employee') stamped on this employee's "
        "petty-cash journal items so the shared advance account can be "
        "reported per employee.",
    )

    def _pc_employee_plan(self):
        """Get-or-create the dedicated 'Employee' analytic plan."""
        Plan = self.env["account.analytic.plan"].sudo()
        plan = Plan.search([("name", "=", PC_EMPLOYEE_PLAN)], limit=1)
        if not plan:
            plan = Plan.create({"name": PC_EMPLOYEE_PLAN})
        return plan

    def _pc_get_analytic_account(self):
        """Return this employee's analytic account, creating it on first use."""
        self.ensure_one()
        if self.pc_analytic_account_id:
            return self.pc_analytic_account_id
        plan = self._pc_employee_plan()
        account = (
            self.env["account.analytic.account"]
            .sudo()
            .create(
                {
                    "name": self.name,
                    "plan_id": plan.id,
                    "company_id": self.company_id.id or self.env.company.id,
                }
            )
        )
        self.sudo().pc_analytic_account_id = account.id
        return account
