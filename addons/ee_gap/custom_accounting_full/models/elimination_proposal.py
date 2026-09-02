# -*- coding: utf-8 -*-
"""Computed elimination proposal — dry-run preview of cancelling entries."""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EliminationProposal(models.Model):
    _name = "custom.elimination.proposal"
    _description = "Intercompany Elimination Proposal"
    _inherit = ["pdp.audited.mixin"]
    _order = "date_to desc, id desc"

    name = fields.Char(
        default=lambda self: self.env._("Elimination Draft"),
        copy=False,
    )
    chart_id = fields.Many2one(
        "custom.consolidation.chart",
        required=True,
        ondelete="cascade",
    )
    rule_id = fields.Many2one(
        "custom.elimination.rule",
        required=True,
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("proposed", "Proposed"),
            ("posted", "Posted"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        copy=False,
        required=True,
    )
    total_amount = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    line_ids = fields.One2many(
        "custom.elimination.proposal.line",
        "proposal_id",
        string="Lines",
    )
    # Spec-aligned alias (computed) for the same lines
    proposed_line_ids = fields.One2many(
        "custom.elimination.proposal.line",
        compute="_compute_proposed_lines",
        string="Proposed Lines",
    )

    @api.depends("line_ids")
    def _compute_proposed_lines(self):
        for rec in self:
            rec.proposed_line_ids = rec.line_ids

    move_id = fields.Many2one(
        "account.move",
        readonly=True,
        copy=False,
    )
    notes = fields.Text()

    # ----- workflow -----
    def action_compute(self):
        """Compute the cancellation amount between the configured account pair."""
        AML = self.env["account.move.line"].sudo()
        ProposalLine = self.env["custom.elimination.proposal.line"]
        for proposal in self:
            proposal.line_ids.unlink()
            rule = proposal.rule_id
            base_domain = [
                ("parent_state", "=", "posted"),
                ("date", ">=", proposal.date_from),
                ("date", "<=", proposal.date_to),
            ]
            a_lines = AML.search(
                base_domain
                + [
                    ("company_id", "=", rule.company_a_id.id),
                    ("account_id", "=", rule.account_a_id.id),
                ]
            )
            b_lines = AML.search(
                base_domain
                + [
                    ("company_id", "=", rule.company_b_id.id),
                    ("account_id", "=", rule.account_b_id.id),
                ]
            )
            a_amount = sum(a_lines.mapped("balance"))
            b_amount = sum(b_lines.mapped("balance"))
            elim = min(abs(a_amount), abs(b_amount))
            ProposalLine.create(
                {
                    "proposal_id": proposal.id,
                    "account_id": rule.account_a_id.id,
                    "company_id": rule.company_a_id.id,
                    "amount": a_amount,
                }
            )
            ProposalLine.create(
                {
                    "proposal_id": proposal.id,
                    "account_id": rule.account_b_id.id,
                    "company_id": rule.company_b_id.id,
                    "amount": b_amount,
                }
            )
            proposal.total_amount = elim
            proposal.state = "proposed"
            proposal.notes = (
                f"A ({rule.company_a_id.name} / {rule.account_a_id.code}): {a_amount:.2f}\n"
                f"B ({rule.company_b_id.name} / {rule.account_b_id.code}): {b_amount:.2f}\n"
                f"Eliminated: {elim:.2f}"
            )
        return True

    def action_post(self):
        for proposal in self:
            if proposal.state != "proposed":
                raise UserError(_("Compute the proposal first."))
            if proposal.total_amount <= 0:
                raise UserError(_("Nothing to eliminate (amount is zero)."))
            move = proposal._make_elimination_move()
            proposal.move_id = move.id
            proposal.state = "posted"

    def action_reject(self):
        for proposal in self:
            proposal.state = "rejected"

    def action_cancel(self):
        for proposal in self:
            if proposal.move_id and proposal.move_id.state == "posted":
                proposal.move_id.button_draft()
                proposal.move_id.button_cancel()
            proposal.state = "cancelled"

    def _make_elimination_move(self):
        self.ensure_one()
        rule = self.rule_id
        Journal = self.env["account.journal"].sudo()
        journal = Journal.search(
            [
                ("company_id", "=", rule.company_a_id.id),
                ("type", "=", "general"),
            ],
            limit=1,
        )
        if not journal:
            raise UserError(
                _(
                    "No general journal in company %(c)s.",
                    c=rule.company_a_id.name,
                )
            )
        return (
            self.env["account.move"]
            .sudo()
            .with_company(rule.company_a_id)
            .create(
                {
                    "journal_id": journal.id,
                    "date": self.date_to,
                    "move_type": "entry",
                    "ref": _("IC Elimination: %(name)s [%(d1)s..%(d2)s]")
                    % {
                        "name": rule.name,
                        "d1": self.date_from,
                        "d2": self.date_to,
                    },
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "account_id": rule.account_a_id.id,
                                "name": _("Eliminate A: %s") % rule.account_a_id.code,
                                "debit": self.total_amount,
                                "credit": 0.0,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "account_id": rule.account_b_id.id,
                                "name": _("Eliminate B: %s") % rule.account_b_id.code,
                                "debit": 0.0,
                                "credit": self.total_amount,
                            },
                        ),
                    ],
                }
            )
        )


class EliminationProposalLine(models.Model):
    _name = "custom.elimination.proposal.line"
    _description = "Elimination Proposal Line (computed)"

    proposal_id = fields.Many2one(
        "custom.elimination.proposal",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one("res.company", required=True)
    account_id = fields.Many2one("account.account", required=True)
    partner_id = fields.Many2one("res.partner", ondelete="set null")
    amount = fields.Float(string="Source Balance")
    justification = fields.Text()
