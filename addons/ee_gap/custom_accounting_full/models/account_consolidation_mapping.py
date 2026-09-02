# -*- coding: utf-8 -*-
"""Per-rule account mapping rows (issuer code → receiver code)."""

from __future__ import annotations

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class IntercompanyAccountMapping(models.Model):
    _name = "account.intercompany.account.mapping"
    _description = "Intercompany Account Mapping"
    _order = "rule_id, source_account_id"

    rule_id = fields.Many2one("account.intercompany.rule", required=True, ondelete="cascade")
    source_account_id = fields.Many2one(
        "account.account",
        string="From Account (issuer)",
        required=True,
        ondelete="restrict",
    )
    target_account_id = fields.Many2one(
        "account.account",
        string="To Account (receiver)",
        required=True,
        ondelete="restrict",
    )
    note = fields.Char()

    _uniq_source_per_rule = models.Constraint(
        "unique (rule_id, source_account_id)",
        "Source account already mapped in this rule.",
    )

    @api.constrains("rule_id", "source_account_id", "target_account_id")
    def _check_company_alignment(self):
        for rec in self:
            if rec.source_account_id and rec.rule_id.company_from_id not in rec.source_account_id.company_ids:
                raise ValidationError("Source account is not in the issuing company's chart.")
            if rec.target_account_id and rec.rule_id.company_to_id not in rec.target_account_id.company_ids:
                raise ValidationError("Target account is not in the receiving company's chart.")
