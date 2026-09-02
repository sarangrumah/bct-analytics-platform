# -*- coding: utf-8 -*-
import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MarketingSegment(models.Model):
    _name = "marketing.segment"
    _description = "Marketing Segment"
    _order = "name"

    name = fields.Char(required=True)
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        ondelete="cascade",
        domain="[('model', 'in', ('res.partner',))]",
        default=lambda s: s.env.ref("base.model_res_partner").id,
    )
    filter_domain = fields.Char(string="Filter Domain", default="[]")
    active = fields.Boolean(default=True)

    contact_count = fields.Integer(compute="_compute_contact_count")

    @api.constrains("filter_domain")
    def _check_domain(self):
        for rec in self:
            try:
                parsed = ast.literal_eval(rec.filter_domain or "[]")
                if not isinstance(parsed, list):
                    raise ValueError("Domain must be a list literal")
            except (SyntaxError, ValueError) as e:
                raise ValidationError(_("Invalid filter_domain: %s") % e) from e

    def _compute_contact_count(self):
        for rec in self:
            try:
                Model = self.env[rec.model_id.model]
                rec.contact_count = Model.sudo().search_count(ast.literal_eval(rec.filter_domain or "[]"))
            except Exception:
                rec.contact_count = 0

    def resolve_partners(self):
        self.ensure_one()
        Model = self.env[self.model_id.model]
        return Model.sudo().search(ast.literal_eval(self.filter_domain or "[]"))
