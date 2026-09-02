# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomFrontdeskStation(models.Model):
    _name = "custom.frontdesk.station"
    _description = "Frontdesk Station"
    _order = "name"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    location_description = fields.Char()
    active = fields.Boolean(default=True)
