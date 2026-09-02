# -*- coding: utf-8 -*-
"""Card BIN -> acquirer / MDR mapping.

MDR (Merchant Discount Rate) is the fee an acquiring bank deducts from a card
settlement. A card's leading digits (BIN) identify the acquirer and thus the
rate. This config model maps BIN ranges to an acquiring bank + MDR rate + the
P&L account the fee is booked to, and exposes helpers the payment flow uses to
resolve the rate and compute the fee. Written generically so a bank-recon or POS
hook can reuse ``_match_bin`` / ``_compute_mdr`` later.
"""

from __future__ import annotations

from odoo import api, fields, models


class RetailMdrBin(models.Model):
    _name = "retail.mdr.bin"
    _description = "Card BIN to Acquirer / MDR Mapping"
    _order = "sequence, bin_from"

    name = fields.Char(string="Label", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    card_scheme = fields.Selection(
        selection=[
            ("visa", "Visa"),
            ("mastercard", "Mastercard"),
            ("jcb", "JCB"),
            ("amex", "Amex"),
            ("gpn", "GPN / Debit"),
            ("other", "Other"),
        ],
        string="Scheme",
    )
    bin_from = fields.Char(
        string="BIN From",
        required=True,
        help="Lower bound of the BIN range (leading card digits). For a single "
        "prefix set BIN From = BIN To. Both endpoints must have the same length.",
    )
    bin_to = fields.Char(
        string="BIN To",
        required=True,
        help="Upper bound of the BIN range (inclusive).",
    )
    acquirer_bank_id = fields.Many2one("res.bank", string="Acquiring Bank")
    mdr_percent = fields.Float(string="MDR %", digits=(16, 4))
    mdr_fixed = fields.Monetary(string="MDR Fixed Fee", currency_field="currency_id")
    mdr_account_id = fields.Many2one(
        "account.account",
        string="MDR Expense Account",
        domain="[('active', '=', True),"
        " ('account_type', 'not in', ('asset_receivable', 'liability_payable', 'off_balance')),"
        " ('company_ids', 'in', company_id)]",
    )
    date_start = fields.Date(string="Effective From")
    date_end = fields.Date(string="Effective To")

    _bin_range_order = models.Constraint(
        "CHECK(bin_from <= bin_to)",
        "BIN From must be less than or equal to BIN To.",
    )

    def _compute_mdr(self, amount):
        """Fee for ``amount`` under this mapping: amount * % + fixed."""
        self.ensure_one()
        return (amount * (self.mdr_percent or 0.0) / 100.0) + (self.mdr_fixed or 0.0)

    @api.model
    def _match_bin(self, card_number, date=None, company=None):
        """Return the most specific effective mapping for ``card_number``.

        Matches records whose ``[bin_from, bin_to]`` range contains the leading
        digits of the card number (compared over the range's own length), that
        are effective on ``date`` for ``company``. The longest / narrowest range
        wins; ``False`` when nothing matches.
        """
        digits = "".join(ch for ch in (card_number or "") if ch.isdigit())
        if not digits:
            return self.browse()
        company = company or self.env.company
        date = date or fields.Date.context_today(self)
        candidates = self.search(
            [
                ("company_id", "=", company.id),
                "|",
                ("date_start", "=", False),
                ("date_start", "<=", date),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", date),
            ]
        )
        best = self.browse()
        best_len = -1
        for rec in candidates:
            length = len(rec.bin_from or "")
            if not length:
                continue
            prefix = digits[:length]
            if len(prefix) < length:
                continue  # card shorter than the range width -> cannot match
            if rec.bin_from <= prefix <= rec.bin_to and length > best_len:
                best, best_len = rec, length
        return best
