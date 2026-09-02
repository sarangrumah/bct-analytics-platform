# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class AppraisalCycle(models.Model):
    _name = "appraisal.cycle"
    _description = "Appraisal Cycle"
    _inherit = ["mail.thread"]
    _order = "period_end desc"

    name = fields.Char(required=True)
    period_start = fields.Date(required=True)
    period_end = fields.Date(required=True)
    template_id = fields.Many2one("appraisal.template", required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("running", "Running"), ("closed", "Closed")],
        default="draft",
        required=True,
        tracking=True,
    )
    department_ids = fields.Many2many(
        "hr.department",
        "appraisal_cycle_dept_rel",
        "cycle_id",
        "dept_id",
        string="Departments (empty = all)",
    )
    appraisal_ids = fields.One2many("appraisal.appraisal", "cycle_id")
    appraisal_count = fields.Integer(compute="_compute_count")
    completed_count = fields.Integer(compute="_compute_count")

    def _compute_count(self):
        for rec in self:
            rec.appraisal_count = len(rec.appraisal_ids)
            rec.completed_count = len(rec.appraisal_ids.filtered(lambda a: a.state == "closed"))

    def action_launch(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft cycles can be launched."))
            Employee = self.env["hr.employee"].sudo()
            domain = [("active", "=", True)]
            if rec.department_ids:
                domain.append(("department_id", "in", rec.department_ids.ids))
            employees = Employee.search(domain)
            Appraisal = self.env["appraisal.appraisal"].sudo()
            for emp in employees:
                if Appraisal.search_count([("cycle_id", "=", rec.id), ("employee_id", "=", emp.id)]):
                    continue
                Appraisal.create(
                    {
                        "cycle_id": rec.id,
                        "employee_id": emp.id,
                        "manager_id": emp.parent_id.id if emp.parent_id else False,
                        "template_id": rec.template_id.id,
                    }
                )
            rec.write({"state": "running"})

    def action_close(self):
        self.write({"state": "closed"})
