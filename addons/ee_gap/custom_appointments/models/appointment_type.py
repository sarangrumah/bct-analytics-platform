# -*- coding: utf-8 -*-
from odoo import fields, models


class AppointmentType(models.Model):
    _name = "appointment.type"
    _description = "Appointment Type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    slug = fields.Char(required=True, index=True, help="URL-friendly identifier — used in the public booking link.")
    description = fields.Html()
    duration_minutes = fields.Integer(default=30, required=True)
    buffer_minutes = fields.Integer(default=0, help="Gap between consecutive bookings.")
    advance_notice_hours = fields.Integer(default=4, help="Minimum lead time required to book.")
    max_days_ahead = fields.Integer(default=30, help="How many days in the future bookings are allowed.")
    require_confirmation = fields.Boolean(
        default=False, help="If checked, bookings start in 'pending' and need internal approval."
    )
    color = fields.Integer()
    active = fields.Boolean(default=True)
    resource_ids = fields.Many2many(
        "appointment.resource",
        "appointment_type_resource_rel",
        "type_id",
        "resource_id",
        string="Available Resources",
    )

    _slug_uniq = models.Constraint(
        "unique(slug)",
        "Appointment slug must be unique.",
    )
