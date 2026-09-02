# -*- coding: utf-8 -*-
from odoo import fields, models


SEVERITY = [
    ("minor", "Minor"),
    ("major", "Major"),
    ("critical", "Critical"),
]

ALERT_STATES = [
    ("open", "Open"),
    ("investigating", "Investigating"),
    ("corrective_action", "Corrective Action"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
]


class QualityAlert(models.Model):
    _name = "quality.alert"
    _description = "Quality Alert (NCR)"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(required=True)
    check_id = fields.Many2one("quality.check", ondelete="set null")
    product_id = fields.Many2one("product.product", index=True)
    severity = fields.Selection(SEVERITY, default="minor", required=True, tracking=True)
    state = fields.Selection(ALERT_STATES, default="open", required=True, tracking=True, index=True)
    owner_id = fields.Many2one("res.users", default=lambda s: s.env.user)
    description = fields.Html()
    root_cause = fields.Html()
    corrective_action = fields.Html()
    resolved_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    capa_ids = fields.One2many(
        "custom.quality.capa",
        "alert_id",
        string="CAPA Actions",
    )
    capa_count = fields.Integer(compute="_compute_capa_count")

    def _compute_capa_count(self):
        for rec in self:
            rec.capa_count = len(rec.capa_ids)

    def _pdp_audit_classification(self):
        return "internal"

    def action_investigate(self):
        for rec in self:
            rec.write({"state": "investigating"})

    def action_corrective(self):
        for rec in self:
            rec.write({"state": "corrective_action"})

    def action_resolve(self):
        for rec in self:
            rec.write({"state": "resolved", "resolved_at": fields.Datetime.now()})
            rec._pdp_audit_write("quality_alert_resolved", rec.id, {"severity": rec.severity})

    def action_close(self):
        for rec in self:
            rec.write({"state": "closed", "closed_at": fields.Datetime.now()})
            rec._pdp_audit_write("quality_alert_closed", rec.id, None)
