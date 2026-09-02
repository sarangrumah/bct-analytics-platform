# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomFixedAssetGroup(models.Model):
    _name = "custom.fixed.asset.group"
    _description = "Custom Fixed Asset Group"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
    )
    default_useful_life_months = fields.Integer(
        string="Default Useful Life (months)",
        default=60,
    )
    default_asset_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Asset Account",
        domain="[('company_ids', 'in', company_id)]",
        help="Balance-sheet account for assets of this group.",
    )
    default_depreciation_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Accumulated Depreciation Account",
        domain="[('company_ids', 'in', company_id)]",
    )
    default_expense_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Depreciation Expense Account",
        domain="[('company_ids', 'in', company_id)]",
    )
    default_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Default Depreciation Journal",
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    default_revaluation_surplus_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Revaluation Surplus Account",
        domain="[('company_ids', 'in', company_id)]",
        help="Equity / OCI account credited for an upward revaluation.",
    )
    default_revaluation_loss_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Revaluation Loss Account",
        domain="[('company_ids', 'in', company_id)]",
        help="P&L expense account debited for a downward revaluation not covered by an existing surplus.",
    )
    default_revaluation_income_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Revaluation Income Account",
        domain="[('company_ids', 'in', company_id)]",
        help="P&L income account credited when an upward revaluation reverses a "
        "prior downward revaluation that was expensed.",
    )
    default_retained_earnings_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Retained Earnings Account",
        domain="[('company_ids', 'in', company_id)]",
        help="Equity account the revaluation surplus is transferred to on disposal.",
    )

    _code_company_unique = models.Constraint(
        "UNIQUE(code, company_id)",
        "Asset group code must be unique per company.",
    )
