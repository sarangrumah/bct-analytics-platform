# -*- coding: utf-8 -*-
from odoo import fields, models


class AttendanceGeofence(models.Model):
    _name = "attendance.geofence"
    _description = "Attendance Geofence"
    _order = "name"

    name = fields.Char(required=True)
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    radius_meters = fields.Integer(default=100)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
