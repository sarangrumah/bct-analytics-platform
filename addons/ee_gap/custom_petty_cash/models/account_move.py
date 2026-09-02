# -*- coding: utf-8 -*-
"""Tag every petty-cash-generated move so requests can gather their own
journal items (smart buttons, settlement reconciliation)."""

from __future__ import annotations

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    petty_cash_request_id = fields.Many2one(
        "petty.cash.request",
        string="Petty Cash Request",
        index=True,
        copy=False,
        help="Petty-cash request this journal entry / bill belongs to.",
    )
    petty_cash_realization_id = fields.Many2one(
        "petty.cash.realization",
        string="Petty Cash Realization",
        index=True,
        copy=False,
    )
