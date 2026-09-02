# -*- coding: utf-8 -*-
"""Vendor Invoice — PO Non-Trade & Non-PO Non-Trade (engagement only).

Vendors submit invoices through the portal against their PO & GR (data pulled
from SAP). On final approval the invoice is pushed to SAP which auto-creates the
MIRO and pays. Odoo posts no ``account.move``.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FinanceVendorInvoice(models.Model):
    _name = "finance.vendor.invoice"
    _inherit = ["finance.document.mixin"]
    _description = "Vendor Invoice (Non-Trade)"
    _sequence_code = "finance.vendor.invoice"

    invoice_subtype = fields.Selection(
        selection=[
            ("po_non_trade", "Invoice Vendor PO - Non Trade"),
            ("non_po_non_trade", "Invoice Vendor Non PO - Non Trade"),
        ],
        string="Invoice Class",
        required=True,
        default="po_non_trade",
        tracking=True,
    )
    vendor_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        required=True,
        domain="[('supplier_rank', '>', 0)]",
        tracking=True,
    )
    invoice_type_id = fields.Many2one("finance.invoice.type", string="Invoice Type")
    routine_type_id = fields.Many2one("finance.invoice.routine.type", string="Routine Type")
    invoice_number = fields.Char(string="Vendor Invoice No", copy=False, tracking=True)
    invoice_date = fields.Date(string="Invoice Date")
    gr_number = fields.Char(string="GR Number", help="Goods Receipt reference from SAP.")
    tax_amount = fields.Monetary(string="Tax Amount", currency_field="currency_id")
    description = fields.Text()
    note = fields.Text()

    line_ids = fields.One2many("finance.vendor.invoice.line", "invoice_id", string="Detail")

    amount_untaxed = fields.Monetary(
        string="Untaxed",
        currency_field="currency_id",
        compute="_compute_amount",
        store=True,
    )
    amount = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_amount",
        store=True,
    )

    @api.depends("line_ids.subtotal", "tax_amount")
    def _compute_amount(self):
        for rec in self:
            rec.amount_untaxed = sum(rec.line_ids.mapped("subtotal"))
            rec.amount = rec.amount_untaxed + (rec.tax_amount or 0.0)

    @api.onchange("invoice_subtype")
    def _onchange_invoice_subtype(self):
        # Non-PO class must not carry a PO/GR reference.
        if self.invoice_subtype == "non_po_non_trade":
            self.po_number = False
            self.gr_number = False

    @api.constrains("invoice_subtype", "po_number", "gr_number")
    def _check_po_reference(self):
        for rec in self:
            if rec.invoice_subtype == "po_non_trade" and not rec.po_number:
                raise UserError(
                    _("A PO Number is required for a PO Non-Trade vendor invoice (%s).") % (rec.name or "?")
                )

    def _finance_sap_payload(self) -> dict:
        vals = super()._finance_sap_payload()
        vals.update(
            {
                "invoice_subtype": self.invoice_subtype,
                "vendor": self.vendor_id.name,
                "vendor_ref": self.vendor_id.ref or "",
                "invoice_number": self.invoice_number or "",
                "invoice_date": self.invoice_date.isoformat() if self.invoice_date else "",
                "gr_number": self.gr_number or "",
                "tax_amount": float(self.tax_amount or 0.0),
                "lines": [
                    {
                        "item": line.item_id.code or line.item_id.name or "",
                        "gl_account": line.account_code or "",
                        "cost_center": line.cost_center_code or "",
                        "quantity": float(line.quantity or 0.0),
                        "amount": float(line.subtotal or 0.0),
                        "description": line.name or "",
                    }
                    for line in self.line_ids
                ],
            }
        )
        return vals


class FinanceVendorInvoiceLine(models.Model):
    _name = "finance.vendor.invoice.line"
    _description = "Vendor Invoice Detail Line"
    _order = "invoice_id, sequence, id"

    invoice_id = fields.Many2one("finance.vendor.invoice", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    item_id = fields.Many2one("finance.item.submission", string="Item")
    account_code = fields.Char(string="GL Account")
    cost_center_code = fields.Char(string="Cost Center")
    currency_id = fields.Many2one(related="invoice_id.currency_id")
    quantity = fields.Float(default=1.0)
    unit_amount = fields.Monetary(string="Unit Amount", currency_field="currency_id")
    subtotal = fields.Monetary(
        string="Subtotal",
        currency_field="currency_id",
        compute="_compute_subtotal",
        store=True,
    )

    @api.depends("quantity", "unit_amount")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (line.quantity or 0.0) * (line.unit_amount or 0.0)
