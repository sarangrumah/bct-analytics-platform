# -*- coding: utf-8 -*-
"""Optional estimate breakdown for a petty cash request."""

from __future__ import annotations

from odoo import fields, models


class PettyCashRequestLine(models.Model):
    _name = "petty.cash.request.line"
    _description = "Petty Cash Request Estimate Line"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "petty.cash.request",
        string="Request",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    account_id = fields.Many2one(
        "account.account",
        string="Expected Account",
        help="Indicative expense/asset account for this estimated spend.",
    )
    amount = fields.Monetary(string="Estimated Amount", currency_field="currency_id")
    currency_id = fields.Many2one(related="request_id.currency_id")
