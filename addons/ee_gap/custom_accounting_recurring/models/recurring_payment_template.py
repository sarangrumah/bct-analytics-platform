# -*- coding: utf-8 -*-
"""Recurring payment templates."""

from __future__ import annotations

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


PERIOD_OFFSETS = {
    "monthly": relativedelta(months=1),
    "quarterly": relativedelta(months=3),
    "yearly": relativedelta(years=1),
}


class RecurringPaymentTemplate(models.Model):
    _name = "custom.recurring.payment.template"
    _description = "Recurring Payment Template"
    _inherit = ["pdp.audited.mixin", "mail.thread"]
    _order = "next_date, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        tracking=True,
    )
    payment_type = fields.Selection(
        [
            ("inbound", "Receive Money"),
            ("outbound", "Send Money"),
        ],
        default="outbound",
        required=True,
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]",
    )
    amount = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    period = fields.Selection(
        [
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("yearly", "Yearly"),
        ],
        default="monthly",
        required=True,
    )
    next_date = fields.Date(
        string="Next Run",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    end_date = fields.Date()
    auto_post = fields.Boolean(
        default=True,
        help="When set, the generated payment is posted automatically.",
    )
    last_generated_at = fields.Datetime(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals.get("code") == _("New"):
                vals["code"] = self.env["ir.sequence"].next_by_code("custom.recurring.journal.template") or _("New")
        return super().create(vals_list)

    @api.constrains("amount")
    def _check_amount(self):
        for tpl in self:
            if tpl.amount <= 0:
                raise ValidationError(
                    _(
                        "Recurring payment '%(name)s': amount must be positive.",
                        name=tpl.name,
                    )
                )

    @api.constrains("end_date", "next_date")
    def _check_end_date(self):
        for tpl in self:
            if tpl.end_date and tpl.next_date and tpl.end_date < tpl.next_date:
                raise ValidationError(
                    _(
                        "Template '%(name)s': end date is before next run date.",
                        name=tpl.name,
                    )
                )

    def action_run_now(self):
        for tpl in self:
            tpl._generate_one()

    def _generate_one(self):
        self.ensure_one()
        if not self.active:
            raise UserError(
                _(
                    "Template '%(name)s' is archived.",
                    name=self.name,
                )
            )
        partner_type = "customer" if self.payment_type == "inbound" else "supplier"
        payment_vals = {
            "partner_id": self.partner_id.id,
            "partner_type": partner_type,
            "payment_type": self.payment_type,
            "journal_id": self.journal_id.id,
            "amount": self.amount,
            "date": self.next_date,
            "memo": self.code or self.name,
            "company_id": self.company_id.id,
        }
        payment = self.env["account.payment"].with_company(self.company_id).create(payment_vals)
        if self.auto_post:
            payment.action_post()
        offset = PERIOD_OFFSETS[self.period]
        self.write(
            {
                "last_generated_at": fields.Datetime.now(),
                "next_date": self.next_date + offset,
            }
        )
        return payment

    @api.model
    def _cron_generate_due(self):
        today = fields.Date.context_today(self)
        due = self.search(
            [
                ("active", "=", True),
                ("next_date", "<=", today),
            ]
        )
        generated = 0
        for tpl in due:
            try:
                if tpl.end_date and tpl.next_date > tpl.end_date:
                    continue
                tpl._generate_one()
                generated += 1
            except Exception as exc:  # noqa: BLE001 - cron resilience
                _logger.exception(
                    "custom.recurring.payment.template: failed on %s: %s",
                    tpl.display_name,
                    exc,
                )
                self.env.cr.rollback()
        return generated
