# -*- coding: utf-8 -*-
from odoo import fields, models


class SignTemplate(models.Model):
    _name = "sign.template"
    _description = "Sign Template"
    _order = "name"

    name = fields.Char(required=True)
    attachment_id = fields.Many2one(
        "ir.attachment",
        required=True,
        ondelete="restrict",
        help="The PDF that signers will see.",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
