# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PlanningSlot(models.Model):
    _name = "planning.slot"
    _description = "Planning Slot"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "start_dt desc"

    name = fields.Char(compute="_compute_name", store=True)
    role_id = fields.Many2one("planning.role", required=True, index=True)
    employee_id = fields.Many2one(
        "hr.employee",
        index=True,
        tracking=True,
        help="Assignee — empty = open shift, anyone in the role can claim it.",
    )
    start_dt = fields.Datetime(required=True, tracking=True)
    end_dt = fields.Datetime(required=True, tracking=True)
    duration_hours = fields.Float(compute="_compute_duration", store=True)
    state = fields.Selection(
        [("open", "Open"), ("assigned", "Assigned"), ("published", "Published"), ("cancelled", "Cancelled")],
        default="open",
        required=True,
        tracking=True,
    )
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("role_id", "employee_id", "start_dt")
    def _compute_name(self):
        for rec in self:
            who = rec.employee_id.name if rec.employee_id else _("Open")
            rec.name = f"{rec.role_id.name}: {who} @ {rec.start_dt}"

    @api.depends("start_dt", "end_dt")
    def _compute_duration(self):
        for rec in self:
            if rec.start_dt and rec.end_dt:
                rec.duration_hours = (rec.end_dt - rec.start_dt).total_seconds() / 3600.0
            else:
                rec.duration_hours = 0.0

    @api.constrains("start_dt", "end_dt", "employee_id")
    def _check_overlap(self):
        for rec in self:
            if rec.start_dt and rec.end_dt and rec.start_dt >= rec.end_dt:
                raise ValidationError(_("End must be after start."))
            if not rec.employee_id:
                continue
            overlap = self.sudo().search(
                [
                    ("employee_id", "=", rec.employee_id.id),
                    ("state", "in", ("assigned", "published")),
                    ("id", "!=", rec.id),
                    ("start_dt", "<", rec.end_dt),
                    ("end_dt", ">", rec.start_dt),
                ],
                limit=1,
            )
            if overlap:
                raise ValidationError(
                    _(
                        "Employee %(emp)s already has a shift overlapping with this slot (%(other)s).",
                        emp=rec.employee_id.name,
                        other=overlap.name,
                    )
                )

    def action_assign(self, employee_id: int):
        for rec in self:
            rec.write({"employee_id": employee_id, "state": "assigned"})
            rec._pdp_audit_write("planning_assign", rec.id, {"employee_id": employee_id})

    def action_publish(self):
        for rec in self:
            if not rec.employee_id:
                raise ValidationError(_("Cannot publish an unassigned slot."))
            rec.write({"state": "published"})
            rec._pdp_audit_write("planning_publish", rec.id, None)

    def action_cancel(self):
        for rec in self:
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("planning_cancel", rec.id, None)
