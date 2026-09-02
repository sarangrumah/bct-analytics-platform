# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    deferred_expense_account_id = fields.Many2one(
        "account.account",
        string="Deferred Expense Account",
        check_company=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_prepayments'))]",
    )
    deferred_revenue_account_id = fields.Many2one(
        "account.account",
        string="Deferred Revenue Account",
        check_company=True,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current'))]",
    )
    deferred_journal_id = fields.Many2one(
        "account.journal",
        string="Deferred Journal",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
