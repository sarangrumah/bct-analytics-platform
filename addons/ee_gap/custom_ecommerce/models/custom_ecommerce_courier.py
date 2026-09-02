# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomEcommerceCourier(models.Model):
    _name = "custom.ecommerce.courier"
    _description = "Indonesian eCommerce Courier"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Selection(
        [
            ("jne", "JNE"),
            ("jnt", "J&T"),
            ("sicepat", "SiCepat"),
            ("anteraja", "AnterAja"),
            ("posindo", "Pos Indonesia"),
            ("grab", "Grab"),
            ("gojek", "Gojek"),
            ("custom", "Custom / Other"),
        ],
        required=True,
        tracking=True,
    )
    api_endpoint = fields.Char(string="API Endpoint")
    api_key = fields.Char(
        string="API Key",
        groups="custom_ecommerce.group_manager",
    )
    tracking_url_template = fields.Char(
        string="Tracking URL Template",
        help="Use {awb} placeholder, e.g. https://www.jne.co.id/id/tracking/trace/{awb}",
    )
    is_active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    service_types = fields.Char(
        string="Service Types",
        help="Comma-separated codes: REG, YES, OKE, etc.",
    )
