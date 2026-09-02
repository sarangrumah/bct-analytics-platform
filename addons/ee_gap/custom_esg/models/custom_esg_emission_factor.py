# -*- coding: utf-8 -*-
"""GHG Scope 1/2/3 emission factor catalog.

Each row encodes a unit-based conversion from an activity input
(litres of fuel, kWh of electricity, kg of waste) into kg CO2-equivalent.
Used by the auto-collect cron to translate accounting transactions
(``account.move.line``) into ``custom.esg.measurement`` rows under the
Scope 1 / 2 / 3 categories.
"""

from __future__ import annotations

from odoo import api, fields, models


class CustomEsgEmissionFactor(models.Model):
    _name = "custom.esg.emission.factor"
    _description = "GHG Emission Factor (Scope 1/2/3)"
    _order = "category, name"

    name = fields.Char(string="Name", required=True)
    category = fields.Selection(
        [
            ("scope_1", "Scope 1 (Direct)"),
            ("scope_2", "Scope 2 (Purchased Energy)"),
            ("scope_3", "Scope 3 (Value Chain)"),
        ],
        string="GHG Scope",
        required=True,
        default="scope_1",
    )
    unit_of_measure = fields.Char(
        string="Unit of Measure",
        required=True,
        help="Activity unit, e.g. kWh, liter, kg, km.",
    )
    kg_co2_per_unit = fields.Float(
        string="kg CO2e / Unit",
        required=True,
        digits=(16, 6),
        help="Emission factor: kilograms of CO2-equivalent per activity unit.",
    )
    source_reference = fields.Char(
        string="Source Reference",
        help="Citation, e.g. 'IPCC AR6 2021 Table 1.4' or 'KESDM 2022'.",
    )
    metric_id = fields.Many2one(
        comodel_name="custom.esg.metric",
        string="Linked Metric",
        ondelete="set null",
        help="Target ESG metric updated when this factor is applied.",
    )
    is_active = fields.Boolean(string="Active", default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    _name_category_uniq = models.Constraint(
        "unique(name, category, company_id)",
        "Emission factor name must be unique within a scope per company.",
    )

    @api.model
    def compute_emission(self, factor_code_or_id, activity_value):
        """Convenience: return kg CO2e for a given activity quantity.

        ``factor_code_or_id`` may be either an integer id or a Char ``name``.
        """
        Factor = self.sudo()
        if isinstance(factor_code_or_id, int):
            factor = Factor.browse(factor_code_or_id)
        else:
            factor = Factor.search([("name", "=", factor_code_or_id)], limit=1)
        if not factor:
            return 0.0
        return float(activity_value or 0.0) * factor.kg_co2_per_unit
