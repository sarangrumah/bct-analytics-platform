# -*- coding: utf-8 -*-
"""One realized spend — either a third-party purchase (→ vendor bill) or a
plain expense (→ direct entry)."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PettyCashRealizationLine(models.Model):
    _name = "petty.cash.realization.line"
    _description = "Petty Cash Realization Line"
    _order = "sequence, id"

    realization_id = fields.Many2one(
        "petty.cash.realization",
        string="Realization",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    line_type = fields.Selection(
        selection=[
            ("third_party", "Third-Party Purchase"),
            ("expense", "Expense"),
        ],
        string="Type",
        required=True,
        default="expense",
        help="Third-Party → full vendor bill (billing, PPN/PPh, payment). "
        "Expense → direct entry Dr Expense / Cr Uang Muka.",
    )
    name = fields.Char(string="Description", required=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        help="Required for third-party purchases — the supplier billed.",
    )
    product_id = fields.Many2one("product.product", string="Product")
    account_id = fields.Many2one(
        "account.account",
        string="Account",
        required=True,
        help="Expense / asset account (COA) charged for this spend.",
    )
    tax_ids = fields.Many2many(
        "account.tax",
        string="Taxes",
        domain="[('type_tax_use', '=', 'purchase')]",
        help="Input VAT (PPN Masukan) for third-party purchases.",
    )
    x_custom_withholding_category_id = fields.Many2one(
        "tax.withholding.category",
        string="Kode Objek PPh",
        help="Optional PPh withholding object code — applied automatically on "
        "the generated vendor bill by custom_tax_id.",
    )
    price_subtotal = fields.Monetary(
        string="Untaxed",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    price_tax = fields.Monetary(
        string="Tax Amount",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    price_total = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
    )
    quantity = fields.Float(default=1.0)
    price_unit = fields.Monetary(string="Amount (untaxed)", currency_field="currency_id", required=True)
    currency_id = fields.Many2one(related="realization_id.currency_id")
    company_id = fields.Many2one(related="realization_id.company_id")
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "petty_cash_line_attachment_rel",
        "line_id",
        "attachment_id",
        string="Invoice / Proof",
        help="Supplier invoice (mandatory for third-party) or expense proof.",
    )

    @api.depends("price_unit", "quantity", "tax_ids")
    def _compute_amounts(self):
        for line in self:
            subtotal = (line.price_unit or 0.0) * (line.quantity or 1.0)
            taxes = (
                line.tax_ids.compute_all(
                    subtotal,
                    currency=line.currency_id,
                    quantity=1.0,
                    partner=line.partner_id,
                )
                if line.tax_ids
                else None
            )
            if taxes:
                line.price_subtotal = taxes["total_excluded"]
                line.price_total = taxes["total_included"]
                line.price_tax = taxes["total_included"] - taxes["total_excluded"]
            else:
                line.price_subtotal = subtotal
                line.price_total = subtotal
                line.price_tax = 0.0

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                if not line.name:
                    line.name = line.product_id.display_name
                if not line.account_id:
                    accounts = line.product_id.product_tmpl_id.get_product_accounts()
                    line.account_id = accounts.get("expense")

    @api.onchange("line_type")
    def _onchange_line_type(self):
        if self.line_type == "expense":
            self.partner_id = False

    @api.constrains("line_type", "partner_id")
    def _check_vendor(self):
        for line in self:
            if line.line_type == "third_party" and not line.partner_id:
                raise UserError(_("Third-party line '%s' requires a vendor.") % (line.name or "?"))
