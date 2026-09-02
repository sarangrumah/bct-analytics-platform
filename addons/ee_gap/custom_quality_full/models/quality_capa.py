# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CustomQualityCapa(models.Model):
    """Corrective and Preventive Action attached to a quality.alert."""

    _name = "custom.quality.capa"
    _description = "Corrective / Preventive Action"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "deadline asc, id desc"

    name = fields.Char(default="New", copy=False)
    alert_id = fields.Many2one(
        "quality.alert",
        required=True,
        ondelete="cascade",
        index=True,
    )
    action_type = fields.Selection(
        [
            ("corrective", "Corrective"),
            ("preventive", "Preventive"),
            ("containment", "Containment"),
        ],
        default="corrective",
        required=True,
    )
    description = fields.Text(required=True)
    responsible_id = fields.Many2one(
        "res.users",
        required=True,
        tracking=True,
        default=lambda s: s.env.user,
    )
    deadline = fields.Date()
    completion_date = fields.Date()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("canceled", "Canceled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )
    signature_ids = fields.One2many(
        "custom.quality.signature",
        "capa_id",
        string="Sign-off Signatures",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.quality.capa") or "CAPA/???"
        return super().create(vals_list)

    def action_start(self):
        for rec in self:
            rec.write({"state": "in_progress"})

    def action_done(self):
        for rec in self:
            rec.write({"state": "done", "completion_date": fields.Date.context_today(rec)})
            # If all CAPAs on the alert are done, mark alert as resolved.
            alert = rec.alert_id
            if alert and all(c.state in ("done", "canceled") for c in alert.capa_ids):
                if alert.state not in ("resolved", "closed"):
                    alert.action_resolve()

    def action_cancel(self):
        for rec in self:
            rec.write({"state": "canceled"})

    def action_reset(self):
        for rec in self:
            rec.write({"state": "draft", "completion_date": False})
