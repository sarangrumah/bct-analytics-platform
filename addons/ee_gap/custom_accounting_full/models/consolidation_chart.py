# -*- coding: utf-8 -*-
"""Consolidation chart: a group-level COA with mapping + elimination rules.

Ported (and adapted) from tenant/era_accounting_consolidation. Distinct from
the existing ``account.consolidation.config`` model — config defines a
*perimeter* (companies + dates), whereas this model defines the *target chart*
that perimeter rolls up into.
"""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class CustomConsolidationChart(models.Model):
    _name = "custom.consolidation.chart"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Consolidation Chart (Group COA)"
    _order = "code, name"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    active = fields.Boolean(default=True)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    company_ids = fields.Many2many(
        "res.company",
        "custom_consol_chart_company_rel",
        "chart_id",
        "company_id",
        string="Companies in Scope",
    )
    account_ids = fields.One2many("custom.consolidation.chart.account", "chart_id", string="Chart Accounts")
    mapping_ids = fields.One2many("custom.consolidation.mapping", "chart_id", string="Mappings")
    elimination_rule_ids = fields.One2many("custom.elimination.rule", "chart_id", string="Elimination Rules")
    state = fields.Selection(
        [("draft", "Draft"), ("locked", "Locked")],
        default="draft",
        required=True,
        tracking=True,
    )
    notes = fields.Text()

    _unique_chart_code = models.Constraint("unique (code)", "Consolidation chart code must be unique.")

    def action_lock(self):
        for rec in self:
            if not rec.account_ids:
                raise ValidationError(_("Cannot lock a chart with no accounts."))
            rec.state = "locked"

    def action_reset_draft(self):
        for rec in self:
            rec.state = "draft"


class CustomConsolidationChartAccount(models.Model):
    _name = "custom.consolidation.chart.account"
    _description = "Consolidation Chart Account"
    _order = "code"

    chart_id = fields.Many2one("custom.consolidation.chart", required=True, ondelete="cascade")
    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)
    account_category = fields.Selection(
        [
            ("asset", "Asset"),
            ("liability", "Liability"),
            ("equity", "Equity"),
            ("income", "Income"),
            ("expense", "Expense"),
            ("off_bs", "Off Balance Sheet"),
        ],
        required=True,
        default="asset",
    )

    _unique_account_code_per_chart = models.Constraint(
        "unique (chart_id, code)",
        "Account code must be unique within a consolidation chart.",
    )
