# -*- coding: utf-8 -*-
from odoo import fields, models


class FSMSite(models.Model):
    _name = "fsm.site"
    _description = "Field Service Site"
    _order = "partner_id, name"
    _inherit = ["pdp.audited.mixin"]

    name = fields.Char(required=True)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade", index=True)
    street = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one("res.country.state")
    zip = fields.Char()
    country_id = fields.Many2one("res.country", default=lambda s: s.env.ref("base.id"))

    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))

    access_notes = fields.Text(help="Gate codes, parking instructions, on-site contact, etc.")
    active = fields.Boolean(default=True)

    work_order_count = fields.Integer(compute="_compute_work_order_count")

    def _compute_work_order_count(self):
        WO = self.env["fsm.work.order"].sudo()
        for rec in self:
            rec.work_order_count = WO.search_count([("site_id", "=", rec.id)])

    def _pdp_audit_classification(self):
        return "pii"  # site address tied to a partner
