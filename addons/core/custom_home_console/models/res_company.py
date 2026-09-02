# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    brand_accent_color = fields.Char(
        string="Home Accent Color",
        default="#714B67",
        help="Hex color used as the accent on the Home Console "
        "(card highlights, spotlight focus ring). Falls back to the "
        "Odoo violet when empty.",
    )
    brand_logo_home = fields.Binary(
        string="Home Console Logo",
        help="Logo shown on the Home Console header. Falls back to the standard company logo when empty.",
    )
    home_announcement_html = fields.Html(
        string="Home Announcement",
        sanitize=True,
        help="Rich-text banner displayed on the Home Console. Users can dismiss it for 24 hours.",
    )
    home_announcement_active = fields.Boolean(
        string="Show Announcement",
        default=False,
    )
