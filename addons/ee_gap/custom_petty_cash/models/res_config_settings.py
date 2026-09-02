# -*- coding: utf-8 -*-
"""Expose petty-cash configuration in Settings."""

from __future__ import annotations

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    petty_cash_advance_account_id = fields.Many2one(
        related="company_id.petty_cash_advance_account_id",
        readonly=False,
    )
    petty_cash_bank_out_journal_id = fields.Many2one(
        related="company_id.petty_cash_bank_out_journal_id",
        readonly=False,
    )
    petty_cash_payment_journal_id = fields.Many2one(
        related="company_id.petty_cash_payment_journal_id",
        readonly=False,
    )
    petty_cash_expense_journal_id = fields.Many2one(
        related="company_id.petty_cash_expense_journal_id",
        readonly=False,
    )
    petty_cash_realization_days = fields.Integer(
        string="Realization Deadline (days)",
        config_parameter="custom_petty_cash.realization_days",
        default=14,
        help="Default number of days after disbursement by which the "
        "employee must submit the realization. Drives the overdue filter "
        "and reminder cron.",
    )
    petty_cash_disburse_via_payment = fields.Boolean(
        string="Disburse via account.payment",
        config_parameter="custom_petty_cash.disburse_via_payment",
        default=False,
        help="When enabled, disbursement is booked through an "
        "account.payment (outbound, bank-reconcilable) instead of a direct "
        "journal entry. The advance account is used as the payment "
        "destination account.",
    )
