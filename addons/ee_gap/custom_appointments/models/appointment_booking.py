# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


BOOKING_STATES = [
    ("pending", "Pending Confirmation"),
    ("confirmed", "Confirmed"),
    ("cancelled", "Cancelled"),
    ("done", "Completed"),
    ("no_show", "No-show"),
]


class AppointmentBooking(models.Model):
    _name = "appointment.booking"
    _description = "Appointment Booking"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "start_dt desc, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)

    type_id = fields.Many2one("appointment.type", required=True, index=True)
    resource_id = fields.Many2one("appointment.resource", required=True, index=True)
    customer_name = fields.Char(required=True, tracking=True)
    customer_email = fields.Char(required=True, tracking=True)
    customer_phone = fields.Char(tracking=True)
    partner_id = fields.Many2one("res.partner", tracking=True)

    start_dt = fields.Datetime(required=True, tracking=True)
    end_dt = fields.Datetime(required=True, tracking=True)

    state = fields.Selection(BOOKING_STATES, default="pending", required=True, tracking=True, index=True)
    notes = fields.Html()
    calendar_event_id = fields.Many2one("calendar.event", readonly=True, copy=False)

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "pii"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("appointment.booking") or "APT-???"
            # Default state: pending if type requires confirmation, else confirmed
            t = self.env["appointment.type"].browse(vals.get("type_id"))
            if t and not t.require_confirmation and "state" not in vals:
                vals["state"] = "confirmed"
        records = super().create(vals_list)
        for rec in records:
            if rec.state == "confirmed":
                rec._sync_calendar_event()
        return records

    @api.constrains("start_dt", "end_dt", "resource_id")
    def _check_slot(self):
        for rec in self:
            if rec.start_dt and rec.end_dt and rec.start_dt >= rec.end_dt:
                raise ValidationError(_("End must be after start."))
            if not rec.start_dt or not rec.end_dt:
                continue
            # Capacity check — count overlapping confirmed bookings
            overlap = self.sudo().search(
                [
                    ("resource_id", "=", rec.resource_id.id),
                    ("state", "=", "confirmed"),
                    ("id", "!=", rec.id),
                    ("start_dt", "<", rec.end_dt),
                    ("end_dt", ">", rec.start_dt),
                ]
            )
            if len(overlap) >= rec.resource_id.capacity:
                raise ValidationError(
                    _(
                        "Resource '%s' is fully booked for this slot (capacity %s).",
                    )
                    % (rec.resource_id.name, rec.resource_id.capacity)
                )

    def action_confirm(self):
        for rec in self:
            if rec.state != "pending":
                raise UserError(_("Only pending bookings can be confirmed."))
            rec.write({"state": "confirmed"})
            rec._sync_calendar_event()
            rec._pdp_audit_write("appointment_confirm", rec.id, None)

    def action_cancel(self):
        for rec in self:
            if rec.state == "cancelled":
                continue
            if rec.calendar_event_id:
                rec.calendar_event_id.sudo().unlink()
            rec.write({"state": "cancelled", "calendar_event_id": False})
            rec._pdp_audit_write("appointment_cancel", rec.id, None)

    def action_done(self):
        for rec in self:
            rec.write({"state": "done"})
            rec._pdp_audit_write("appointment_done", rec.id, None)

    def action_no_show(self):
        for rec in self:
            rec.write({"state": "no_show"})
            rec._pdp_audit_write("appointment_no_show", rec.id, None)

    def _sync_calendar_event(self):
        self.ensure_one()
        if not self.resource_id.user_id:
            return
        Event = self.env["calendar.event"].sudo()
        vals = {
            "name": f"{self.type_id.name}: {self.customer_name}",
            "start": self.start_dt,
            "stop": self.end_dt,
            "user_id": self.resource_id.user_id.id,
            "partner_ids": [(6, 0, [self.partner_id.id] if self.partner_id else [])],
            "description": f"Customer: {self.customer_name} <{self.customer_email}>",
        }
        if self.calendar_event_id:
            self.calendar_event_id.write(vals)
        else:
            self.calendar_event_id = Event.create(vals).id
