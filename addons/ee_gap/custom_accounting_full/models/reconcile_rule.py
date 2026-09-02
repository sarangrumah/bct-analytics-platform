# -*- coding: utf-8 -*-
"""Bank statement reconciliation rules + automatic matching cron."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ReconcileRule(models.Model):
    _name = "custom.reconcile.rule"
    _description = "Bank Statement Reconcile Rule"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    journal_ids = fields.Many2many(
        "account.journal",
        "custom_reconcile_rule_journal_rel",
        "rule_id",
        "journal_id",
        string="Journals",
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        help="Leave empty to match all bank/cash journals in the company.",
    )
    match_partner = fields.Boolean(default=True)
    match_amount = fields.Boolean(default=True)
    amount_tolerance = fields.Float(default=0.0)
    match_reference_regex = fields.Char(
        string="Reference Regex",
        help="Optional Python regex tested against the statement line payment_ref/ref/narration.",
    )
    match_date_window_days = fields.Integer(
        default=7,
        help="Search candidate move lines whose date is within +/- N days of the statement line.",
    )
    payment_match_partner_field = fields.Selection(
        [
            ("partner_id", "Same Partner"),
            ("parent_id", "Same Commercial Partner"),
        ],
        default="partner_id",
        required=True,
    )
    target_account_id = fields.Many2one(
        "account.account",
        help="Counterpart account for unmatched residual (bank fees, interest, write-offs).",
    )
    auto_validate = fields.Boolean(
        default=False,
        help="When checked, the rule reconciles statement lines without human confirmation.",
    )

    @api.constrains("match_reference_regex")
    def _check_regex(self):
        for rule in self:
            if rule.match_reference_regex:
                try:
                    re.compile(rule.match_reference_regex)
                except re.error as exc:
                    raise ValidationError(
                        _(
                            "Rule '%(name)s' regex is invalid: %(err)s",
                            name=rule.name,
                            err=str(exc),
                        )
                    ) from exc

    # ---------- matching ----------
    def _candidate_move_lines(self, stmt_line):
        self.ensure_one()
        AML = self.env["account.move.line"].sudo()
        domain = [
            ("company_id", "=", stmt_line.company_id.id),
            ("parent_state", "=", "posted"),
            ("reconciled", "=", False),
            ("account_id.account_type", "in", ("asset_receivable", "liability_payable")),
        ]
        if self.match_partner and stmt_line.partner_id:
            if self.payment_match_partner_field == "parent_id":
                domain.append(
                    (
                        "partner_id.commercial_partner_id",
                        "=",
                        stmt_line.partner_id.commercial_partner_id.id,
                    )
                )
            else:
                domain.append(("partner_id", "=", stmt_line.partner_id.id))
        elif self.match_partner and not stmt_line.partner_id:
            return AML.browse()
        if self.match_date_window_days and stmt_line.date:
            window = timedelta(days=self.match_date_window_days)
            d_low = fields.Date.to_date(stmt_line.date) - window
            d_high = fields.Date.to_date(stmt_line.date) + window
            domain += [("date", ">=", d_low), ("date", "<=", d_high)]
        candidates = AML.search(domain)
        return candidates.filtered(lambda ml: self._line_matches(stmt_line, ml))

    def _line_matches(self, stmt_line, move_line):
        self.ensure_one()
        if self.match_amount:
            stmt_amt = abs(stmt_line.amount)
            line_amt = abs(move_line.amount_residual)
            if abs(stmt_amt - line_amt) > max(0.005, self.amount_tolerance):
                return False
        if self.match_reference_regex:
            stmt_ref = stmt_line.payment_ref or stmt_line.ref or stmt_line.narration or ""
            try:
                if not re.search(self.match_reference_regex, stmt_ref or ""):
                    return False
            except re.error:
                return False
        return True

    @api.model
    def _cron_auto_reconcile(self):
        """Daily entry-point — iterate per-company and apply rules."""
        Companies = self.env["res.company"].sudo().search([])
        matched_total = 0
        for company in Companies:
            StmtLine = self.env["account.bank.statement.line"].sudo()
            lines = StmtLine.search(
                [
                    ("company_id", "=", company.id),
                    ("is_reconciled", "=", False),
                ],
                limit=1000,
            )
            for line in lines:
                if line._custom_apply_reconcile_rules():
                    matched_total += 1
        _logger.info("custom.reconcile cron matched=%s lines", matched_total)
        return matched_total

    @api.model
    def _cron_apply_rules(self):
        """Walk unmatched bank statement lines and apply rules in order."""
        StmtLine = self.env["account.bank.statement.line"].sudo()
        unmatched = StmtLine.search([("is_reconciled", "=", False)])
        matched = 0
        for line in unmatched:
            applied = line._custom_apply_reconcile_rules()
            if applied:
                matched += 1
        return matched


class AccountBankStatementLineReconcile(models.Model):
    _inherit = "account.bank.statement.line"

    custom_reconcile_rule_id = fields.Many2one(
        "custom.reconcile.rule",
        string="Auto-Reconcile Rule",
        readonly=True,
        copy=False,
    )
    custom_auto_matched = fields.Boolean(
        string="Auto-Matched",
        readonly=True,
        copy=False,
    )

    def _custom_applicable_rules(self):
        self.ensure_one()
        Rule = self.env["custom.reconcile.rule"].sudo()
        rules = Rule.search(
            [
                ("active", "=", True),
                ("company_id", "=", self.company_id.id),
            ]
        )
        return rules.filtered(lambda r: not r.journal_ids or self.journal_id in r.journal_ids)

    def _custom_apply_reconcile_rules(self):
        """Try each applicable rule until one matches; return True if matched."""
        self.ensure_one()
        if self.is_reconciled:
            return False
        for rule in self._custom_applicable_rules():
            candidates = rule._candidate_move_lines(self)
            if not candidates:
                continue
            best = min(
                candidates,
                key=lambda ml: abs(abs(ml.amount_residual) - abs(self.amount)),
            )
            self.custom_reconcile_rule_id = rule.id
            if rule.auto_validate:
                try:
                    counterpart = self.move_id.line_ids.filtered(
                        lambda l: (
                            l.account_id.account_type
                            in (
                                "asset_receivable",
                                "liability_payable",
                            )
                        )
                    )
                    if counterpart:
                        (counterpart + best).reconcile()
                        self.custom_auto_matched = True
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "Auto reconcile failed for stmt line %s: %s",
                        self.id,
                        exc,
                    )
            return True
        return False
