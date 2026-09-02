# -*- coding: utf-8 -*-
"""Recurring journal entry templates."""

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


class RecurringJournalTemplate(models.Model):
    _name = "custom.recurring.journal.template"
    _description = "Recurring Journal Entry Template"
    _inherit = ["pdp.audited.mixin", "mail.thread"]
    _order = "next_date, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        help="Sequence reference (REC/YYYYMM/00001).",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'general')]",
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
    end_date = fields.Date(
        help="Optional: stop generating entries once next run would be after this date.",
    )
    auto_post = fields.Boolean(
        default=True,
        help="When set, generated journal entries are posted automatically.",
    )
    last_generated_at = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many(
        "custom.recurring.journal.template.line",
        "template_id",
        string="Lines",
        copy=True,
    )
    generated_move_ids = fields.One2many(
        "account.move",
        "custom_recurring_template_id",
        readonly=True,
        string="Generated Entries",
    )
    generated_count = fields.Integer(
        compute="_compute_generated_count",
    )

    @api.depends("generated_move_ids")
    def _compute_generated_count(self):
        for rec in self:
            rec.generated_count = len(rec.generated_move_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals.get("code") == _("New"):
                vals["code"] = self.env["ir.sequence"].next_by_code("custom.recurring.journal.template") or _("New")
        return super().create(vals_list)

    @api.constrains("line_ids")
    def _check_balanced(self):
        for tpl in self:
            if not tpl.line_ids:
                continue
            total_debit = sum(tpl.line_ids.mapped("debit"))
            total_credit = sum(tpl.line_ids.mapped("credit"))
            if round(total_debit - total_credit, 2) != 0:
                raise ValidationError(
                    _(
                        "Recurring template '%(name)s' is unbalanced: debit=%(d).2f credit=%(c).2f.",
                        name=tpl.name,
                        d=total_debit,
                        c=total_credit,
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

    # ---------- generation ----------

    def action_run_now(self):
        for tpl in self:
            tpl._generate_one()

    def action_view_moves(self):
        """Open generated journal entries (account.move) for this template."""
        self.ensure_one()
        return {
            "name": "Generated Entries",
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("ref", "like", self.code or self.name)],
        }

    def _generate_one(self):
        """Generate one journal entry from the template; advance next_date."""
        self.ensure_one()
        if not self.active:
            raise UserError(
                _(
                    "Template '%(name)s' is archived.",
                    name=self.name,
                )
            )
        if not self.line_ids:
            raise UserError(
                _(
                    "Template '%(name)s' has no lines.",
                    name=self.name,
                )
            )
        move_vals = {
            "journal_id": self.journal_id.id,
            "date": self.next_date,
            "ref": self.code or self.name,
            "move_type": "entry",
            "company_id": self.company_id.id,
            "custom_recurring_template_id": self.id,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": line.name or self.name,
                        "account_id": line.account_id.id,
                        "partner_id": line.partner_id.id or False,
                        "debit": line.debit,
                        "credit": line.credit,
                        "analytic_distribution": line.analytic_distribution or False,
                    },
                )
                for line in self.line_ids
            ],
        }
        move = self.env["account.move"].with_company(self.company_id).create(move_vals)
        if self.auto_post:
            move.action_post()
        offset = PERIOD_OFFSETS[self.period]
        new_next_date = self.next_date + offset
        self.write(
            {
                "last_generated_at": fields.Datetime.now(),
                "next_date": new_next_date,
            }
        )
        return move

    @api.model
    def _cron_generate_due(self):
        """Cron entry: generate all templates whose next_date <= today."""
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
                    "custom.recurring.journal.template: failed on %s: %s",
                    tpl.display_name,
                    exc,
                )
                self.env.cr.rollback()
        return generated


class AccountMoveRecurringLink(models.Model):
    _inherit = "account.move"

    custom_recurring_template_id = fields.Many2one(
        "custom.recurring.journal.template",
        readonly=True,
        copy=False,
        index=True,
        help="Template that generated this journal entry (if any).",
    )
