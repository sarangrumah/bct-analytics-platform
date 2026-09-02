# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


WO_STATES = [
    ("draft", "Draft"),
    ("scheduled", "Scheduled"),
    ("in_progress", "In Progress"),
    ("on_hold", "On Hold"),
    ("done", "Completed"),
    ("cancelled", "Cancelled"),
]


class FSMWorkOrder(models.Model):
    _name = "fsm.work.order"
    _description = "Field Service Work Order"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "scheduled_start desc, id desc"

    name = fields.Char(required=True, default="New", readonly=True, copy=False)
    site_id = fields.Many2one("fsm.site", required=True, index=True)
    partner_id = fields.Many2one(related="site_id.partner_id", store=True)
    technician_id = fields.Many2one("fsm.technician", required=True, index=True, tracking=True)

    description = fields.Html()
    required_skill_ids = fields.Many2many(
        "fsm.skill",
        "fsm_wo_required_skill_rel",
        "wo_id",
        "skill_id",
        string="Required Skills",
    )

    scheduled_start = fields.Datetime(required=True, tracking=True)
    scheduled_end = fields.Datetime(required=True, tracking=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    duration_hours = fields.Float(compute="_compute_duration", store=True)

    state = fields.Selection(WO_STATES, default="draft", required=True, tracking=True, index=True)

    material_ids = fields.One2many(
        "fsm.work.order.material",
        "work_order_id",
        string="Materials Consumed",
    )
    notes_completion = fields.Text()
    customer_signature = fields.Binary(string="Customer Signature")
    signed_at = fields.Datetime(readonly=True)
    signed_by_name = fields.Char()

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("started_at", "completed_at")
    def _compute_duration(self):
        for rec in self:
            if rec.started_at and rec.completed_at:
                delta = rec.completed_at - rec.started_at
                rec.duration_hours = delta.total_seconds() / 3600.0
            else:
                rec.duration_hours = 0.0

    def _pdp_audit_classification(self):
        return "internal"

    # ----- Lifecycle -----

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("fsm.work.order") or "WO-???"
        return super().create(vals_list)

    @api.constrains("scheduled_start", "scheduled_end")
    def _check_schedule(self):
        for rec in self:
            if rec.scheduled_start and rec.scheduled_end and rec.scheduled_start >= rec.scheduled_end:
                raise UserError(_("Scheduled end must be after start."))

    @api.constrains("required_skill_ids", "technician_id")
    def _check_skills(self):
        for rec in self:
            if not rec.required_skill_ids:
                continue
            missing = rec.required_skill_ids - rec.technician_id.skill_ids
            if missing:
                raise UserError(
                    _(
                        "Technician %(tech)s lacks required skill(s): %(skills)s",
                        tech=rec.technician_id.name,
                        skills=", ".join(missing.mapped("name")),
                    )
                )

    def action_schedule(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft work orders can be scheduled."))
            rec.write({"state": "scheduled"})
            rec._pdp_audit_write("fsm_wo_scheduled", rec.id, None)

    def action_start(self):
        for rec in self:
            if rec.state not in ("scheduled", "on_hold"):
                raise UserError(_("Cannot start from state %s.") % rec.state)
            rec.write({"state": "in_progress", "started_at": fields.Datetime.now()})
            rec._pdp_audit_write("fsm_wo_start", rec.id, None)

    def action_hold(self):
        for rec in self:
            if rec.state != "in_progress":
                raise UserError(_("Only in-progress work orders can be held."))
            rec.write({"state": "on_hold"})
            rec._pdp_audit_write("fsm_wo_hold", rec.id, None)

    def action_complete(self):
        for rec in self:
            if rec.state not in ("in_progress", "on_hold"):
                raise UserError(_("Cannot complete from state %s.") % rec.state)
            rec.write({"state": "done", "completed_at": fields.Datetime.now()})
            rec._pdp_audit_write("fsm_wo_complete", rec.id, {"duration_hours": rec.duration_hours})

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(_("Cannot cancel a completed work order."))
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("fsm_wo_cancel", rec.id, None)

    def action_capture_signature(self, signature_b64: str, signed_by: str):
        self.ensure_one()
        self.write(
            {
                "customer_signature": signature_b64,
                "signed_by_name": signed_by,
                "signed_at": fields.Datetime.now(),
            }
        )
        self._pdp_audit_write("fsm_wo_signature_captured", self.id, {"signed_by": signed_by})
