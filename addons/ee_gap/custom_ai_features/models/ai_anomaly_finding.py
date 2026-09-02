# -*- coding: utf-8 -*-
from odoo import api, fields, models


SEVERITY = [
    ("info", "Info"),
    ("warning", "Warning"),
    ("critical", "Critical"),
]

FINDING_STATES = [
    ("new", "New"),
    ("triaged", "Triaged"),
    ("dismissed", "Dismissed (false positive)"),
    ("resolved", "Resolved"),
]


class AiAnomalyFinding(models.Model):
    _name = "ai.anomaly.finding"
    _description = "AI Anomaly Finding"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "score desc, create_date desc"

    name = fields.Char(compute="_compute_name", store=True)
    scan_id = fields.Many2one("ai.anomaly.scan", ondelete="set null", index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    res_ref = fields.Reference(
        selection="_selection_target_model",
        compute="_compute_res_ref",
        store=False,
    )
    res_name = fields.Char(compute="_compute_res_name", store=True)
    metric = fields.Char(required=True)
    latest_value = fields.Float()
    severity = fields.Selection(SEVERITY, default="info", required=True, index=True, tracking=True)
    score = fields.Float(default=0.0)
    rationale = fields.Text()
    suggested_action = fields.Text()
    state = fields.Selection(FINDING_STATES, default="new", required=True, tracking=True, index=True)
    triaged_by_id = fields.Many2one("res.users", readonly=True)
    triaged_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("res_model", "res_id", "metric")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.res_model}#{rec.res_id} / {rec.metric}"

    @api.model
    def _selection_target_model(self):
        return [(m.model, m.name) for m in self.env["ir.model"].sudo().search([])]

    def _compute_res_ref(self):
        for rec in self:
            rec.res_ref = f"{rec.res_model},{rec.res_id}" if rec.res_model and rec.res_id else False

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        for rec in self:
            if rec.res_model and rec.res_id and rec.res_model in self.env:
                target = self.env[rec.res_model].sudo().browse(rec.res_id)
                rec.res_name = target.display_name if target.exists() else ""
            else:
                rec.res_name = ""

    def _pdp_audit_classification(self):
        return "internal"

    def action_open_source(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
        }

    def action_triage(self):
        for rec in self:
            rec.write({"state": "triaged", "triaged_by_id": self.env.user.id, "triaged_at": fields.Datetime.now()})
            rec._pdp_audit_write("anomaly_triaged", rec.id, None)

    def action_dismiss(self):
        for rec in self:
            rec.write({"state": "dismissed"})
            rec._pdp_audit_write("anomaly_dismissed", rec.id, {"reason": "false_positive"})

    def action_resolve(self):
        for rec in self:
            rec.write({"state": "resolved"})
            rec._pdp_audit_write("anomaly_resolved", rec.id, None)
