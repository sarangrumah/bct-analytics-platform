# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    deferred_expense_account_id = fields.Many2one(related="company_id.deferred_expense_account_id", readonly=False)
    deferred_revenue_account_id = fields.Many2one(related="company_id.deferred_revenue_account_id", readonly=False)
    deferred_journal_id = fields.Many2one(related="company_id.deferred_journal_id", readonly=False)
