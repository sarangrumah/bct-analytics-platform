# -*- coding: utf-8 -*-
from odoo import fields, models


class PpobProductClass(models.Model):
    _name = "custom.ppob.product.class"
    _description = "PPOB Product Class (TELKO, NON_TELKO, ...)"
    _order = "sequence, code"

    code = fields.Char(required=True, help="Short code (e.g., TELKO).")
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    default_wallet_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Wallet Liability Account",
        domain="[('account_type', '=', 'liability_current')]",
        help="Account that mitra wallets of this class post to. Used when auto-creating wallets.",
    )
    default_revenue_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Revenue Account",
        domain="[('account_type', '=', 'income')]",
    )
    default_cogs_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default COGS Account",
        domain="[('account_type', '=', 'expense_direct_cost')]",
    )

    vat_mode = fields.Selection(
        selection=[
            ("margin", "Margin DPP (sell - cost) x rate"),
            ("other_valuation", "DPP Nilai Lain (10/11 x sell) x rate"),
            ("gross", "Gross sell x rate"),
            ("exempt", "VAT Exempt"),
        ],
        default="margin",
        required=True,
        help="How PPN (VAT) is computed for transactions of this class. "
        "See PMK-63/2022 for pulsa/voucher distributor valuation.",
    )

    _code_uniq = models.Constraint(
        "unique(code)",
        "Product class code must be unique.",
    )
