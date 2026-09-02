# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


APPRAISAL_STATES = [
    ("draft", "Draft"),
    ("self_review", "Self Review"),
    ("manager_review", "Manager Review"),
    ("calibration", "Calibration"),
    ("closed", "Closed"),
]


class Appraisal(models.Model):
    _name = "appraisal.appraisal"
    _description = "Employee Appraisal"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "cycle_id, employee_id"

    cycle_id = fields.Many2one("appraisal.cycle", required=True, ondelete="cascade", index=True)
    employee_id = fields.Many2one("hr.employee", required=True, index=True, tracking=True)
    manager_id = fields.Many2one("hr.employee", string="Reviewer (Manager)", tracking=True)
    template_id = fields.Many2one("appraisal.template", required=True)
    state = fields.Selection(APPRAISAL_STATES, default="draft", required=True, tracking=True, index=True)

    line_ids = fields.One2many("appraisal.line", "appraisal_id", string="Items")

    overall_score = fields.Float(compute="_compute_overall_score", store=True)
    overall_comment_employee = fields.Text()
    overall_comment_manager = fields.Text()
    submitted_at_employee = fields.Datetime(readonly=True)
    submitted_at_manager = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)

    _uniq_cycle_employee = models.Constraint(
        "unique(cycle_id, employee_id)",
        "Employee already has an appraisal for this cycle.",
    )

    def _pdp_audit_classification(self):
        return "sensitive_pii"  # performance ratings are sensitive HR data

    @api.depends("line_ids.score_manager", "line_ids.weight")
    def _compute_overall_score(self):
        for rec in self:
            total_w = sum(rec.line_ids.mapped("weight")) or 1.0
            weighted = sum(l.score_manager * l.weight for l in rec.line_ids)
            rec.overall_score = round(weighted / total_w, 2)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Line = self.env["appraisal.line"].sudo()
        for rec in records:
            for item in rec.template_id.item_ids:
                Line.create(
                    {
                        "appraisal_id": rec.id,
                        "template_item_id": item.id,
                        "name": item.name,
                        "competency": item.competency,
                        "weight": item.weight,
                    }
                )
        return records

    def action_start_self_review(self):
        for rec in self:
            rec.write({"state": "self_review"})
            rec._pdp_audit_write("appraisal_self_review_start", rec.id, None)

    def action_submit_self(self):
        for rec in self:
            if rec.state != "self_review":
                raise UserError(_("Can only submit during self review."))
            rec.write({"state": "manager_review", "submitted_at_employee": fields.Datetime.now()})
            rec._pdp_audit_write("appraisal_self_submitted", rec.id, None)

    def action_submit_manager(self):
        for rec in self:
            if rec.state != "manager_review":
                raise UserError(_("Can only submit during manager review."))
            rec.write({"state": "calibration", "submitted_at_manager": fields.Datetime.now()})
            rec._pdp_audit_write("appraisal_manager_submitted", rec.id, {"overall_score": float(rec.overall_score)})

    def action_close(self):
        for rec in self:
            rec.write({"state": "closed", "closed_at": fields.Datetime.now()})
            rec._pdp_audit_write("appraisal_closed", rec.id, {"overall_score": float(rec.overall_score)})
