# -*- coding: utf-8 -*-
"""Reimbursement & Expenses request (engagement only — no GL posting).

Distinct from ``custom_expenses`` (which posts ``account.payment`` against
Odoo's own ledger). Here Odoo is a portal: the approved reimbursement is pushed
to SAP, which posts the GL and pays. Receipt OCR can still be reused from
``custom_ai_bridge`` on the attachment if desired (left as a follow-up hook).
"""

from __future__ import annotations

from odoo import api, fields, models


class FinanceReimbursement(models.Model):
    _name = "finance.reimbursement"
    _inherit = ["finance.document.mixin"]
    _description = "Reimbursement & Expenses Request"
    _sequence_code = "finance.reimbursement"

    submission_type_id = fields.Many2one(
        "finance.submission.type",
        string="Submission Type",
        domain="[('category', 'in', ('reimbursement', 'perjadin', 'non_perjadin'))]",
    )
    expense_date = fields.Date(string="Expense Date")
    payment_method = fields.Selection(
        selection=[("transfer", "Bank Transfer"), ("cash", "Cash")],
        default="transfer",
    )
    bank_name = fields.Char(string="Bank Name")
    account_number = fields.Char(string="A/C Number")
    description = fields.Text()
    note = fields.Text()

    line_ids = fields.One2many("finance.reimbursement.line", "reimbursement_id", string="Detail")

    amount = fields.Monetary(
        string="Amount",
        currency_field="currency_id",
        compute="_compute_amount",
        store=True,
    )

    @api.depends("line_ids.subtotal")
    def _compute_amount(self):
        for rec in self:
            rec.amount = sum(rec.line_ids.mapped("subtotal"))

    def _finance_sap_payload(self) -> dict:
        vals = super()._finance_sap_payload()
        vals["lines"] = [
            {
                "item": line.item_id.code or line.item_id.name or "",
                "gl_account": line.account_code or "",
                "cost_center": line.cost_center_code or "",
                "amount": float(line.subtotal or 0.0),
                "description": line.name or "",
            }
            for line in self.line_ids
        ]
        return vals


class FinanceReimbursementLine(models.Model):
    _name = "finance.reimbursement.line"
    _description = "Reimbursement Detail Line"
    _order = "reimbursement_id, sequence, id"

    reimbursement_id = fields.Many2one("finance.reimbursement", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    item_id = fields.Many2one("finance.item.submission", string="Item")
    account_code = fields.Char(string="GL Account")
    cost_center_code = fields.Char(string="Cost Center")
    currency_id = fields.Many2one(related="reimbursement_id.currency_id")
    subtotal = fields.Monetary(string="Amount", currency_field="currency_id")
    receipt = fields.Binary(string="Receipt", attachment=True)
    receipt_filename = fields.Char()
