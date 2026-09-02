# -*- coding: utf-8 -*-
"""Lines of a recurring journal entry template."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class RecurringJournalTemplateLine(models.Model):
    _name = "custom.recurring.journal.template.line"
    _description = "Recurring Journal Entry Template Line"
    _order = "template_id, sequence, id"

    template_id = fields.Many2one(
        "custom.recurring.journal.template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Label")
    account_id = fields.Many2one(
        "account.account",
        required=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    partner_id = fields.Many2one("res.partner")
    company_id = fields.Many2one(
        related="template_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="template_id.currency_id",
        readonly=True,
    )
    debit = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
    )
    credit = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
    )
    analytic_distribution = fields.Json(
        string="Analytic Distribution",
        help="Same JSON format as account.move.line.analytic_distribution: {'analytic_account_id': percentage, ...}.",
    )

    @api.constrains("debit", "credit")
    def _check_amounts(self):
        for line in self:
            if line.debit < 0 or line.credit < 0:
                raise ValidationError(_("Debit and credit amounts must be zero or positive."))
            if line.debit and line.credit:
                raise ValidationError(_("A line cannot have both debit and credit amounts."))
