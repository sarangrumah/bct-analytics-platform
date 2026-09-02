# -*- coding: utf-8 -*-
"""Intercompany elimination rule definition."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EliminationRule(models.Model):
    _name = "custom.elimination.rule"
    _description = "Intercompany Elimination Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    chart_id = fields.Many2one(
        "custom.consolidation.chart",
        required=True,
        ondelete="cascade",
    )
    company_a_id = fields.Many2one(
        "res.company",
        string="Company A",
        required=True,
        ondelete="restrict",
    )
    company_b_id = fields.Many2one(
        "res.company",
        string="Company B",
        required=True,
        ondelete="restrict",
    )
    account_a_id = fields.Many2one(
        "account.account",
        required=True,
        ondelete="restrict",
        domain="[('company_ids', 'in', company_a_id)]",
        help="Account in company A to be eliminated.",
    )
    account_b_id = fields.Many2one(
        "account.account",
        required=True,
        ondelete="restrict",
        domain="[('company_ids', 'in', company_b_id)]",
        help="Counterpart account in company B.",
    )
    match_partner_id = fields.Many2one(
        "res.partner",
        string="Match Partner",
        ondelete="set null",
        help="Restrict matching to this partner across both companies.",
    )
    threshold_amount = fields.Monetary(
        default=0.0,
        currency_field="currency_id",
        help="Residual variance below this is treated as immaterial.",
    )
    currency_id = fields.Many2one(related="chart_id.currency_id", store=True)
    # Legacy fields preserved for backward compatibility
    match_type = fields.Selection(
        [
            ("exact", "Exact (account-totals)"),
            ("by_partner", "By Partner (matching IC partner)"),
            ("by_reference", "By Reference (move ref equality)"),
        ],
        default="exact",
        required=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        help="Used when match type = 'by_partner' (legacy; prefer match_partner_id).",
    )
    description = fields.Text()

    @api.constrains("company_a_id", "company_b_id")
    def _check_distinct_companies(self):
        for rule in self:
            if rule.company_a_id == rule.company_b_id:
                raise ValidationError(
                    _(
                        "Elimination rule '%(name)s': companies A and B must differ.",
                        name=rule.name,
                    )
                )
