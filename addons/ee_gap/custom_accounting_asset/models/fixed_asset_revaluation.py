# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomFixedAssetRevaluation(models.Model):
    _name = "custom.fixed.asset.revaluation"
    _description = "Custom Fixed Asset Revaluation"
    _order = "revaluation_date desc, id desc"

    asset_id = fields.Many2one(
        comodel_name="custom.fixed.asset",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="asset_id.company_id",
        store=True,
    )
    currency_id = fields.Many2one(
        related="asset_id.currency_id",
        store=True,
    )
    name = fields.Char(
        string="Reference",
        readonly=True,
    )
    revaluation_date = fields.Date(
        required=True,
    )
    net_book_value_before = fields.Monetary(
        string="NBV Before",
        currency_field="currency_id",
        readonly=True,
    )
    new_value = fields.Monetary(
        string="New Value",
        currency_field="currency_id",
        readonly=True,
    )
    revaluation_amount = fields.Monetary(
        string="Adjustment",
        currency_field="currency_id",
        readonly=True,
        help="New value minus net book value before. Positive = upward revaluation.",
    )
    surplus_movement = fields.Monetary(
        string="Surplus Movement",
        currency_field="currency_id",
        readonly=True,
        help="Net change to the equity revaluation surplus (positive = credited).",
    )
    pl_movement = fields.Monetary(
        string="P&L Movement",
        currency_field="currency_id",
        readonly=True,
        help="Net effect on P&L (positive = income/reversal, negative = loss).",
    )
    surplus_balance_after = fields.Monetary(
        string="Surplus Balance After",
        currency_field="currency_id",
        readonly=True,
    )
    loss_recognized_after = fields.Monetary(
        string="Loss Recognized After",
        currency_field="currency_id",
        readonly=True,
    )
    useful_life_before = fields.Integer(
        string="Useful Life Before (months)",
        readonly=True,
    )
    remaining_life_after = fields.Integer(
        string="Remaining Life After (months)",
        readonly=True,
    )
    surplus_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Surplus Account",
        readonly=True,
    )
    loss_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Loss Account",
        readonly=True,
    )
    income_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revaluation Income Account",
        readonly=True,
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        readonly=True,
    )
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        copy=False,
    )
    note = fields.Text()
