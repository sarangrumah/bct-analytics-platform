# -*- coding: utf-8 -*-
"""Per-company mapping from local account → consolidation chart account."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomConsolidationMapping(models.Model):
    _name = "custom.consolidation.mapping"
    _description = "Consolidation Account Mapping"
    _order = "chart_id, company_id, source_account_id"
    _check_company_auto = True

    chart_id = fields.Many2one("custom.consolidation.chart", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade")
    source_account_id = fields.Many2one(
        "account.account",
        string="Local Account",
        required=True,
        ondelete="restrict",
        check_company=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    target_account_id = fields.Many2one(
        "custom.consolidation.chart.account",
        string="Group Account",
        required=True,
        ondelete="restrict",
        domain="[('chart_id', '=', chart_id)]",
    )
    fx_method = fields.Selection(
        [
            ("avg", "Average Rate"),
            ("closing", "Closing Rate"),
            ("historical", "Historical Rate"),
        ],
        default="closing",
        required=True,
    )
    weight = fields.Float(default=1.0, required=True)

    _unique_mapping = models.Constraint(
        "unique (chart_id, company_id, source_account_id)",
        "A local account is already mapped in this chart for this company.",
    )

    @api.constrains("source_account_id", "company_id")
    def _check_account_company(self):
        for rec in self:
            if rec.source_account_id and rec.company_id not in rec.source_account_id.company_ids:
                raise ValidationError(
                    _("Source account %s is not available in company %s.")
                    % (rec.source_account_id.code, rec.company_id.name)
                )

    @api.constrains("weight")
    def _check_weight(self):
        for rec in self:
            if rec.weight <= 0:
                raise ValidationError(_("Weight must be strictly positive."))
