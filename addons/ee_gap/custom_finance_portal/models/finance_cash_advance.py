# -*- coding: utf-8 -*-
"""Cash Advance request + realization (engagement only — no GL posting)."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FinanceCashAdvance(models.Model):
    _name = "finance.cash.advance"
    _inherit = ["finance.document.mixin"]
    _description = "Cash Advance Request"
    _sequence_code = "finance.cash.advance"

    submission_type_id = fields.Many2one(
        "finance.submission.type",
        string="Submission Type",
        domain="[('category', 'in', ('cash_advance', 'perjadin', 'non_perjadin'))]",
    )
    ca_type = fields.Selection(
        selection=[
            ("operational", "Operational"),
            ("travel", "Travel / Perjalanan Dinas"),
            ("project", "Project"),
            ("other", "Other"),
        ],
        string="Cash Advance Type",
        default="operational",
    )
    # NIK / account_number are personal data (PDP "specific"); masked for users
    # lacking custom_pdp_masking.group_unmask_specific.
    nik = fields.Char(string="NIK", related="requester_id.identification_id", store=True, readonly=True)
    purpose = fields.Text(string="CA Purpose")
    payment_date = fields.Date(string="Planned Payment Date (CA)")
    payment_method = fields.Selection(
        selection=[("transfer", "Bank Transfer"), ("cash", "Cash")],
        default="transfer",
    )
    bank_name = fields.Char(string="Bank Name")
    account_number = fields.Char(string="A/C Number")
    recipient = fields.Char(string="Recipient")
    description = fields.Text()
    note = fields.Text()

    line_ids = fields.One2many("finance.cash.advance.line", "advance_id", string="Detail")
    realization_ids = fields.One2many("finance.cash.advance.realization", "advance_id", string="Realizations")
    realization_count = fields.Integer(compute="_compute_realization_count")

    # Override the mixin's plain amount → computed from lines.
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

    def _compute_realization_count(self):
        for rec in self:
            rec.realization_count = len(rec.realization_ids)

    def action_open_realizations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Realizations"),
            "res_model": "finance.cash.advance.realization",
            "view_mode": "list,form",
            "domain": [("advance_id", "=", self.id)],
            "context": {"default_advance_id": self.id, "default_requester_id": self.requester_id.id},
        }

    def _finance_sap_payload(self) -> dict:
        vals = super()._finance_sap_payload()
        vals.update(
            {
                "ca_type": self.ca_type,
                "purpose": self.purpose or "",
                "payment_method": self.payment_method,
                "lines": [
                    {
                        "item": line.item_id.code or line.item_id.name or "",
                        "gl_account": line.account_code or "",
                        "cost_center": line.cost_center_code or "",
                        "amount": float(line.subtotal or 0.0),
                        "description": line.name or "",
                    }
                    for line in self.line_ids
                ],
            }
        )
        return vals


class FinanceCashAdvanceLine(models.Model):
    _name = "finance.cash.advance.line"
    _description = "Cash Advance Detail Line"
    _order = "advance_id, sequence, id"

    advance_id = fields.Many2one("finance.cash.advance", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    item_id = fields.Many2one("finance.item.submission", string="Item")
    # GL coding is reference-only (SAP posts). Stored as codes synced from SAP.
    account_code = fields.Char(string="GL Account")
    cost_center_code = fields.Char(string="Cost Center")
    currency_id = fields.Many2one(related="advance_id.currency_id")
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


class FinanceCashAdvanceRealization(models.Model):
    _name = "finance.cash.advance.realization"
    _inherit = ["finance.document.mixin"]
    _description = "Cash Advance Realization"
    _sequence_code = "finance.cash.advance.realization"

    advance_id = fields.Many2one(
        "finance.cash.advance",
        string="Cash Advance",
        required=True,
        ondelete="restrict",
    )
    realization_date = fields.Date(string="Realization Date")
    note = fields.Text()
    line_ids = fields.One2many("finance.cash.advance.realization.line", "realization_id", string="Detail")

    advance_amount = fields.Monetary(
        string="CA Amount",
        related="advance_id.amount",
        currency_field="currency_id",
        store=True,
    )
    amount = fields.Monetary(
        string="Realized Amount",
        currency_field="currency_id",
        compute="_compute_amount",
        store=True,
    )
    difference = fields.Monetary(
        string="Difference (return / top-up)",
        currency_field="currency_id",
        compute="_compute_amount",
        store=True,
        help="Positive = employee returns cash; negative = company reimburses.",
    )

    @api.depends("line_ids.subtotal", "advance_amount")
    def _compute_amount(self):
        for rec in self:
            rec.amount = sum(rec.line_ids.mapped("subtotal"))
            rec.difference = (rec.advance_amount or 0.0) - rec.amount

    @api.constrains("advance_id")
    def _check_advance_approved(self):
        for rec in self:
            if rec.advance_id and rec.advance_id.state in ("draft", "rejected", "cancelled"):
                raise UserError(_("Cannot realize a Cash Advance that is not yet approved (%s).") % rec.advance_id.name)


class FinanceCashAdvanceRealizationLine(models.Model):
    _name = "finance.cash.advance.realization.line"
    _description = "Cash Advance Realization Detail Line"
    _order = "realization_id, sequence, id"

    realization_id = fields.Many2one("finance.cash.advance.realization", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    item_id = fields.Many2one("finance.item.submission", string="Item")
    account_code = fields.Char(string="GL Account")
    cost_center_code = fields.Char(string="Cost Center")
    currency_id = fields.Many2one(related="realization_id.currency_id")
    subtotal = fields.Monetary(string="Amount", currency_field="currency_id")
