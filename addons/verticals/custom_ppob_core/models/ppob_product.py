# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PpobProduct(models.Model):
    _name = "custom.ppob.product"
    _description = "PPOB Product / SKU"
    _order = "class_id, denom, code"

    code = fields.Char(
        required=True,
        copy=False,
        help="Internal SKU (e.g., TSEL5, PLN20K, BPJS).",
    )
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)

    class_id = fields.Many2one(
        comodel_name="custom.ppob.product.class",
        string="Class",
        required=True,
        ondelete="restrict",
    )
    denom = fields.Monetary(
        string="Denomination",
        currency_field="currency_id",
        help="Nominal value for fixed-denomination products (pulsa, token). "
        "0 means variable / open amount (PLN postpaid, BPJS).",
    )
    cost_price_default = fields.Monetary(
        string="Default Cost Price",
        currency_field="currency_id",
        help="Default buy price. Per-provider override on custom.ppob.provider.sku.map takes precedence.",
    )
    inquiry_required = fields.Boolean(
        string="Requires Inquiry",
        default=False,
        help="Check for products that need a 2-step flow (inquiry, then pay): PLN postpaid, BPJS, PDAM, etc.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    revenue_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Revenue Account",
        domain="[('account_type', '=', 'income')]",
        help="Overrides the product class default.",
    )
    cogs_account_id = fields.Many2one(
        comodel_name="account.account",
        string="COGS Account",
        domain="[('account_type', '=', 'expense_direct_cost')]",
        help="Overrides the product class default.",
    )

    _code_uniq = models.Constraint(
        "unique(code)",
        "PPOB product code must be unique.",
    )

    def _get_revenue_account(self):
        self.ensure_one()
        return self.revenue_account_id or self.class_id.default_revenue_account_id

    def _get_cogs_account(self):
        self.ensure_one()
        return self.cogs_account_id or self.class_id.default_cogs_account_id

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else rec.name
