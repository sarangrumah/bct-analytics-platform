# -*- coding: utf-8 -*-
from odoo import api, fields, models

BANK_CODES = [
    ("BCA", "BCA"),
    ("BNI", "BNI"),
    ("BRI", "BRI"),
    ("MANDIRI", "Mandiri"),
    ("PERMATA", "Permata"),
]


class PpobVaAccount(models.Model):
    _name = "custom.ppob.va.account"
    _description = "Mitra Virtual Account"
    _order = "bank_code, va_number"
    _rec_name = "display_name"

    mitra_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="restrict",
        domain=[("x_custom_ppob_is_mitra", "=", True)],
    )
    wallet_id = fields.Many2one(
        "custom.ppob.wallet",
        required=True,
        ondelete="restrict",
        help="Wallet this VA tops up.",
    )
    bank_code = fields.Selection(selection=BANK_CODES, required=True, default="BCA")
    va_number = fields.Char(required=True, help="Full VA number as issued by the bank.")
    mode = fields.Selection(
        selection=[
            ("manual", "Manual (CSV import)"),
            ("h2h", "Host-to-Host API"),
        ],
        default="manual",
        required=True,
    )
    active = fields.Boolean(default=True)
    transit_account_id = fields.Many2one(
        "account.account",
        string="Transit Account",
        domain="[('account_type', 'in', ['liability_current', 'asset_cash'])]",
        default=lambda self: self.env["custom.ppob.account.mapping"]._get_account("cash_bca_escrow").id or False,
        help="Account used on the debit leg when the VA topup is posted "
        "(usually the BCA escrow cash or unidentified-receipts transit).",
    )
    output_tax_id = fields.Many2one(
        "account.tax",
        string="Output Tax (PPN inclusive)",
        domain="[('type_tax_use', '=', 'sale'), ('amount_type', '=', 'percent'), ('price_include_override', '=', 'tax_included')]",
        help="If set, every VA topup is treated as a tax-inclusive sale to the "
        "mitra: gross = DPP (wallet credit) + PPN (Output VAT). Leave blank "
        "to keep the 2-line posting where the wallet grows by the full gross.",
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _bank_va_uniq = models.Constraint(
        "unique(bank_code, va_number)",
        "VA number must be unique per bank.",
    )

    @api.depends("bank_code", "va_number", "mitra_id.display_name")
    def _compute_display_name(self):
        for va in self:
            va.display_name = f"{va.bank_code} {va.va_number} / {va.mitra_id.display_name or ''}"

    @api.model
    def _find_by_va_number(self, bank_code, va_number):
        return self.search(
            [
                ("bank_code", "=", bank_code),
                ("va_number", "=", va_number),
                ("active", "=", True),
            ],
            limit=1,
        )
