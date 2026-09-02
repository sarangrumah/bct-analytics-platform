# -*- coding: utf-8 -*-
"""Wizard to close a fiscal year: validates + locks period + optional closing entry."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FiscalYearCloseWizard(models.TransientModel):
    _name = "custom.fiscal.year.close.wizard"
    _description = "Close Fiscal Year Wizard"

    fiscal_year_id = fields.Many2one(
        "custom.fiscal.year",
        required=True,
    )
    company_id = fields.Many2one(
        related="fiscal_year_id.company_id",
        readonly=True,
    )
    date_to = fields.Date(related="fiscal_year_id.date_to", readonly=True)
    draft_move_count = fields.Integer(
        compute="_compute_draft_move_count",
    )

    generate_closing_entry = fields.Boolean(
        string="Generate Closing Entry",
        default=False,
        help="If checked, generate a closing journal entry that moves all "
        "P&L balances into Retained Earnings on the period end-date.",
    )
    retained_earnings_account_id = fields.Many2one(
        "account.account",
        string="Retained Earnings Account",
        domain="[('company_ids', 'in', company_id), ('account_type', '=', 'equity')]",
    )
    closing_journal_id = fields.Many2one(
        "account.journal",
        domain="[('company_id', '=', company_id), ('type', '=', 'general')]",
    )

    @api.depends("fiscal_year_id")
    def _compute_draft_move_count(self):
        Move = self.env["account.move"]
        for w in self:
            fy = w.fiscal_year_id
            if not fy:
                w.draft_move_count = 0
                continue
            w.draft_move_count = Move.search_count(
                [
                    ("company_id", "=", fy.company_id.id),
                    ("date", ">=", fy.date_from),
                    ("date", "<=", fy.date_to),
                    ("state", "=", "draft"),
                ]
            )

    def action_close(self):
        self.ensure_one()
        fy = self.fiscal_year_id
        if fy.state == "closed":
            raise UserError(_("Fiscal year is already closed."))
        if self.draft_move_count:
            raise UserError(
                _(
                    "Cannot close fiscal year '%(name)s' — %(n)d draft journal "
                    "entry(ies) still exist in the period. Post or delete them "
                    "first.",
                    name=fy.name,
                    n=self.draft_move_count,
                )
            )
        if self.generate_closing_entry:
            if not self.retained_earnings_account_id or not self.closing_journal_id:
                raise UserError(
                    _(
                        "To generate a closing entry, both the Retained Earnings "
                        "account and the closing journal must be set."
                    )
                )
            self._generate_closing_entry()

        fy.company_id.fiscalyear_lock_date = fy.date_to
        fy.state = "closed"
        return {"type": "ir.actions.act_window_close"}

    def _generate_closing_entry(self):
        """Sweep P&L balances into Retained Earnings.

        Sums posted ``account.move.line`` rows whose account.account_type is
        income/expense over the fiscal year, then produces one balanced
        journal entry that zeroes each P&L account and books the net to
        Retained Earnings.
        """
        self.ensure_one()
        fy = self.fiscal_year_id
        AML = self.env["account.move.line"].sudo()
        income_types = (
            "income",
            "income_other",
        )
        expense_types = (
            "expense",
            "expense_depreciation",
            "expense_direct_cost",
        )

        domain_base = [
            ("parent_state", "=", "posted"),
            ("company_id", "=", fy.company_id.id),
            ("date", ">=", fy.date_from),
            ("date", "<=", fy.date_to),
        ]
        income_lines = AML.search(domain_base + [("account_id.account_type", "in", income_types)])
        expense_lines = AML.search(domain_base + [("account_id.account_type", "in", expense_types)])
        per_account: dict[int, float] = {}
        for line in income_lines + expense_lines:
            per_account.setdefault(line.account_id.id, 0.0)
            per_account[line.account_id.id] += line.balance
        if not per_account:
            return False
        move_lines = []
        net = 0.0
        for acc_id, balance in per_account.items():
            if abs(balance) < 0.005:
                continue
            # Reverse the balance to zero the account
            if balance > 0:
                move_lines.append(
                    (
                        0,
                        0,
                        {
                            "account_id": acc_id,
                            "name": _("Closing entry"),
                            "debit": 0.0,
                            "credit": balance,
                        },
                    )
                )
            else:
                move_lines.append(
                    (
                        0,
                        0,
                        {
                            "account_id": acc_id,
                            "name": _("Closing entry"),
                            "debit": -balance,
                            "credit": 0.0,
                        },
                    )
                )
            net += balance
        # Net profit → credit retained earnings; net loss → debit.
        if net > 0:
            move_lines.append(
                (
                    0,
                    0,
                    {
                        "account_id": self.retained_earnings_account_id.id,
                        "name": _("Net Profit %s") % fy.name,
                        "debit": net,
                        "credit": 0.0,
                    },
                )
            )
        elif net < 0:
            move_lines.append(
                (
                    0,
                    0,
                    {
                        "account_id": self.retained_earnings_account_id.id,
                        "name": _("Net Loss %s") % fy.name,
                        "debit": 0.0,
                        "credit": -net,
                    },
                )
            )
        if not move_lines:
            return False
        move = (
            self.env["account.move"]
            .with_company(fy.company_id)
            .create(
                {
                    "journal_id": self.closing_journal_id.id,
                    "date": fy.date_to,
                    "move_type": "entry",
                    "ref": _("Closing entry %s") % fy.name,
                    "line_ids": move_lines,
                }
            )
        )
        move.action_post()
        return move
