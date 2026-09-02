# -*- coding: utf-8 -*-
"""Per-company petty-cash accounting configuration."""

from __future__ import annotations

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    petty_cash_advance_account_id = fields.Many2one(
        "account.account",
        string="Petty Cash Advance Account",
        domain="[('reconcile', '=', True)]",
        help="Reconcilable asset account 'Uang Muka Petty Cash'. Debited on "
        "disbursement (per employee), credited as the employee realizes or "
        "returns the money; clears to zero when a request is settled.",
    )
    petty_cash_bank_out_journal_id = fields.Many2one(
        "account.journal",
        string="Petty Cash Bank-Out Journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Journal whose bank/cash account funds the disbursement "
        "(Cr Bank) and receives the returned cash (Dr Bank).",
    )
    petty_cash_payment_journal_id = fields.Many2one(
        "account.journal",
        string="Petty Cash Payment Journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Journal used to pay third-party vendor bills out of the "
        "advance. Its payment-method outstanding accounts are auto-pointed "
        "at the Petty Cash Advance account, so paying a bill books "
        "Dr AP / Cr Uang Muka Petty Cash.",
    )
    petty_cash_expense_journal_id = fields.Many2one(
        "account.journal",
        string="Petty Cash Expense Journal",
        domain="[('type', '=', 'general')]",
        help="Miscellaneous journal for non-third-party expense realizations "
        "(Dr Expense / Cr Uang Muka Petty Cash). Falls back to the first "
        "general journal when unset.",
    )
