# -*- coding: utf-8 -*-
"""Corporate card registry — links an employee + bank journal + masked PAN."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomExpenseCorporateCard(models.Model):
    _name = "custom.expense.corporate.card"
    _description = "Corporate Card"
    _inherit = ["mail.thread"]
    _order = "employee_id, name"

    name = fields.Char(
        string="Card Label",
        required=True,
        tracking=True,
    )
    masked_number = fields.Char(
        string="Masked Card Number",
        required=True,
        tracking=True,
        help="Display format only, e.g. '**** **** **** 1234'. Never store the full PAN.",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Cardholder",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    bank_journal_id = fields.Many2one(
        "account.journal",
        string="Bank Journal",
        required=True,
        domain=[("type", "in", ("bank", "cash"))],
        ondelete="restrict",
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    expense_count = fields.Integer(
        string="Expenses",
        compute="_compute_expense_count",
    )

    _unique_masked_per_employee = models.Constraint(
        "unique(employee_id, masked_number, company_id)",
        "A corporate card with this masked number is already registered for this employee.",
    )

    @api.constrains("masked_number")
    def _check_masked_number(self):
        """Reject any string that looks like a full PAN."""
        pan_like = re.compile(r"^\s*(\d[\s-]?){13,19}\s*$")
        for card in self:
            if not card.masked_number:
                continue
            cleaned = card.masked_number.strip()
            digits = re.sub(r"\D", "", cleaned)
            if pan_like.match(cleaned) and len(digits) >= 13 and "*" not in cleaned:
                raise ValidationError(
                    _(
                        "Masked card number must contain mask characters (e.g. "
                        "'**** **** **** 1234'). Storing a full PAN is forbidden."
                    )
                )

    def _compute_expense_count(self):
        Expense = self.env["hr.expense"].sudo()
        for card in self:
            card.expense_count = Expense.search_count(
                [
                    ("x_corporate_card_id", "=", card.id),
                ]
            )

    def action_view_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Expenses for %s") % self.name,
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [("x_corporate_card_id", "=", self.id)],
        }
