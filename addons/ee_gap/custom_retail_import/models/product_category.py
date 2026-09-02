# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    property_account_sales_discount_categ_id = fields.Many2one(
        "account.account",
        company_dependent=True,
        string="Sales Discount Account",
        domain="[('account_type', '=', 'income'), ('deprecated', '=', False)]",
        help="Contra-revenue account this category's discounts are booked to "
        "(e.g. Sales Discount-Textile). Used by the retail import discount reclass.",
    )
    property_account_sales_return_categ_id = fields.Many2one(
        "account.account",
        company_dependent=True,
        string="Sales Return Account",
        domain="[('account_type', '=', 'income'), ('deprecated', '=', False)]",
        help="Contra-revenue account this category's customer returns are booked to "
        "(e.g. Sales Return-textile). Used by the retail import X48 refund posting.",
    )
