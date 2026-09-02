# -*- coding: utf-8 -*-
from odoo import api, fields, models

FOREIGN_MOVE_TYPES = (
    "out_invoice",
    "out_refund",
    "out_receipt",
    "in_invoice",
    "in_refund",
    "in_receipt",
)


class AccountMove(models.Model):
    _inherit = "account.move"

    x_fx_is_foreign = fields.Boolean(
        string="Foreign Currency Document",
        compute="_compute_x_fx_is_foreign",
        help="The document is an invoice/bill/receipt written in a currency other than the company currency.",
    )
    x_fx_rate_company_per_unit = fields.Float(
        string="Rate",
        compute="_compute_x_fx_rate_company_per_unit",
        digits=(16, 4),
        help="How many units of the company currency one unit of the document "
        "currency is worth, i.e. the inverse of the stored invoice currency "
        "rate. Display only.",
    )

    @api.depends("move_type", "currency_id", "company_currency_id")
    def _compute_x_fx_is_foreign(self):
        for move in self:
            move.x_fx_is_foreign = bool(
                move.move_type in FOREIGN_MOVE_TYPES
                and move.currency_id
                and move.company_currency_id
                and move.currency_id != move.company_currency_id
            )

    @api.depends("invoice_currency_rate")
    def _compute_x_fx_rate_company_per_unit(self):
        for move in self:
            rate = move.invoice_currency_rate
            move.x_fx_rate_company_per_unit = (1.0 / rate) if rate else 0.0
