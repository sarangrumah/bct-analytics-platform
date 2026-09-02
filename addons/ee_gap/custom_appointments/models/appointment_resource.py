# -*- coding: utf-8 -*-
from odoo import fields, models


class AppointmentResource(models.Model):
    _name = "appointment.resource"
    _description = "Appointment Resource"
    _order = "name"

    name = fields.Char(required=True)
    user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    timezone = fields.Char(default="Asia/Jakarta")
    capacity = fields.Integer(default=1, help="Number of simultaneous bookings the resource can accept.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    working_hours_start = fields.Float(default=9.0, help="24h decimal e.g. 9.0 = 09:00")
    working_hours_end = fields.Float(default=17.0)
    working_days = fields.Char(
        default="1,2,3,4,5",
        help="Comma-separated ISO weekday numbers (1=Mon, 7=Sun)",
    )
