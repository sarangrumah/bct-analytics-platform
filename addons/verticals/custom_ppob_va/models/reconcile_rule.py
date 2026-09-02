# -*- coding: utf-8 -*-
"""Extend custom.reconcile.rule with a VA-topup rule type.

A ``va_match`` rule matches a bank-statement line's reference against a
``custom.ppob.va.account`` and creates + credits a ``custom.ppob.va.topup``
instead of reconciling against an AR/AP move line. Integrated into the platform
reconcile flow by overriding ``_custom_apply_reconcile_rules`` on the statement
line so VA rules run before the standard AR/AP matching.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .ppob_va_account import BANK_CODES


class ReconcileRule(models.Model):
    _inherit = "custom.reconcile.rule"

    rule_type = fields.Selection(
        selection=[
            ("default", "Default (open AR/AP matching)"),
            ("va_match", "Virtual Account Topup"),
        ],
        default="default",
        required=True,
    )
    va_bank_code = fields.Selection(
        selection=BANK_CODES,
        help="Only used when rule_type=va_match. Restricts the VA lookup to this bank.",
    )
    va_extract_regex = fields.Char(
        string="VA Extract Regex",
        help="Python regex with a named capture group ``va`` that extracts the "
        "VA number from the statement payment_ref.",
    )

    @api.constrains("rule_type", "va_extract_regex")
    def _check_va_regex(self):
        for rule in self:
            if rule.rule_type == "va_match" and rule.va_extract_regex:
                try:
                    re.compile(rule.va_extract_regex)
                except re.error as exc:
                    raise ValidationError(_("Invalid VA extract regex: %s") % exc) from exc

    def _apply_va_match(self, statement_line):
        """Match the VA number in a statement line and create/credit a topup.
        Returns the topup when matched, else False."""
        self.ensure_one()
        if self.rule_type != "va_match":
            return False
        payload = statement_line.payment_ref or statement_line.ref or ""
        if not payload:
            return False
        va_number = None
        if self.va_extract_regex:
            m = re.search(self.va_extract_regex, payload)
            if m:
                va_number = m.groupdict().get("va") or (m.group(1) if m.groups() else None)
        if not va_number:
            m = re.search(r"\b(\d{6,20})\b", payload)
            if m:
                va_number = m.group(1)
        if not va_number:
            return False

        bank_code = self.va_bank_code or "BCA"
        va = self.env["custom.ppob.va.account"]._find_by_va_number(bank_code, va_number)
        if not va:
            return False

        Topup = self.env["custom.ppob.va.topup"]
        details = statement_line.transaction_details if isinstance(statement_line.transaction_details, dict) else {}
        bank_ref = details.get("ref") or statement_line.ref or statement_line.payment_ref
        existing = Topup.search([("bank_ref", "=", bank_ref)], limit=1)
        if existing:
            return existing
        topup = Topup.create(
            {
                "va_account_id": va.id,
                "amount": abs(statement_line.amount or 0.0),
                "bank_ref": bank_ref or f"{statement_line.id}",
                "source": "csv_import",
                "state": "settled",
                "statement_line_id": statement_line.id,
            }
        )
        topup.action_credit_wallet()
        return topup


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    def _custom_apply_reconcile_rules(self):
        """Run VA-topup rules first; fall back to the standard AR/AP matching."""
        self.ensure_one()
        if not self.is_reconciled:
            va_rules = self._custom_applicable_rules().filtered(lambda r: r.rule_type == "va_match")
            for rule in va_rules:
                topup = rule._apply_va_match(self)
                if topup:
                    self.custom_reconcile_rule_id = rule.id
                    return True
        return super()._custom_apply_reconcile_rules()
