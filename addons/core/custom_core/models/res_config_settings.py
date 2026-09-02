# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    custom_platform_label = fields.Char(
        string="Custom Platform",
        readonly=True,
        default="Odoo 19 Platform — settings anchored here by downstream modules.",
    )
