# -*- coding: utf-8 -*-
"""Transfer-order rule definition.

``source_location_domain`` and ``target_location_domain`` are stored as text
and evaluated via ``safe_eval`` at engine-evaluation time. The Domain
widget would force a model-bound editor; we want admins to express open
expressions without a server restart.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


TRIGGER = [
    ("low_water_mark", "Low Water Mark"),
    ("expiry_approaching", "Expiry Approaching"),
    ("zone_consolidation", "Zone Consolidation"),
    ("picking_replenishment", "Picking Replenishment"),
    ("manual", "Manual"),
]


class ToRule(models.Model):
    _name = "custom.to.rule"
    _description = "Transfer Order Rule"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "sequence, id"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    warehouse_id = fields.Many2one("stock.warehouse", index=True)
    trigger = fields.Selection(TRIGGER, default="manual", required=True, tracking=True)

    source_location_domain = fields.Char(
        string="Source Location Domain",
        help="Odoo domain expression (text) evaluated via safe_eval to select sources.",
    )
    target_location_domain = fields.Char(
        string="Target Location Domain",
        help="Odoo domain expression (text) evaluated via safe_eval to select targets.",
    )
    product_filter_json = fields.Json(string="Product Filter (JSON)")
    low_water_qty = fields.Float(default=0.0)
    expiry_days_ahead = fields.Integer(
        default=7,
        help="For expiry_approaching trigger: alert when lot expiry <= today + N days.",
    )
    schedule_cron = fields.Char(
        string="Schedule Hint",
        help="Informational cron expression; real cron is module-managed.",
    )
    priority = fields.Integer(default=10)
    last_run_at = fields.Datetime(readonly=True)
    schedule_interval_minutes = fields.Integer(default=15)

    @api.constrains("source_location_domain", "target_location_domain")
    def _check_domains(self):
        for rec in self:
            for raw in (rec.source_location_domain, rec.target_location_domain):
                if not raw:
                    continue
                try:
                    val = safe_eval(raw, {"__builtins__": {}})
                except Exception as exc:
                    raise ValidationError(_("Invalid domain: %s") % exc) from exc
                if not isinstance(val, list):
                    raise ValidationError(_("Domain must evaluate to a list."))

    def _eval_domain(self, raw):
        if not raw:
            return []
        try:
            return safe_eval(raw, {"__builtins__": {}})
        except Exception:
            return []
