# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    x_custom_ppob_is_mitra = fields.Boolean(
        string="Is PPOB Mitra",
        default=False,
        help="Check this if the partner is a PPOB B2B mitra (reseller) that "
        "holds wallets and can submit PPOB transactions.",
    )
    x_custom_ppob_is_provider = fields.Boolean(
        string="Is PPOB Provider",
        default=False,
        help="Check this if the partner is a PPOB provider (telco, PLN, BPJS, "
        "aggregator) from whom PPOB pre-purchases deposits.",
    )
    x_custom_ppob_mitra_code = fields.Char(
        string="Mitra Code",
        copy=False,
        help="Unique short code for the mitra (e.g., MTR001). Used on virtual "
        "account derivation and transaction prefix.",
    )
    x_custom_ppob_mitra_tier_id = fields.Many2one(
        comodel_name="custom.ppob.price.tier",
        string="Price Tier",
        help="Tier that resolves the selling price for each PPOB product.",
    )
    x_custom_ppob_daily_txn_cap = fields.Monetary(
        string="Daily Transaction Cap",
        currency_field="currency_id",
        help="Maximum total IDR value of PPOB sales per calendar day. 0 = no cap.",
    )
    x_custom_ppob_monthly_txn_cap = fields.Monetary(
        string="Monthly Transaction Cap",
        currency_field="currency_id",
        help="Maximum total IDR value of PPOB sales per calendar month. 0 = no cap.",
    )
    x_custom_ppob_has_npwp = fields.Boolean(
        string="Has NPWP",
        compute="_compute_x_custom_ppob_has_npwp",
        store=True,
        help="Derived from the partner's tax id (vat / NPWP). Drives the PPh "
        "withholding rate on commission payouts (2% with NPWP, 4% without).",
    )

    @api.depends("vat")
    def _compute_x_custom_ppob_has_npwp(self):
        for partner in self:
            partner.x_custom_ppob_has_npwp = bool(partner.vat)

    _x_custom_ppob_mitra_code_uniq = models.Constraint(
        "unique(x_custom_ppob_mitra_code)",
        "Mitra code must be unique across partners.",
    )

    @api.constrains("x_custom_ppob_is_mitra", "x_custom_ppob_mitra_code")
    def _check_ppob_mitra_code(self):
        for partner in self:
            if partner.x_custom_ppob_is_mitra and not partner.x_custom_ppob_mitra_code:
                raise ValidationError('Mitra code is required when "Is PPOB Mitra" is set.')
