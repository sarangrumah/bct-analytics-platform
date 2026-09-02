# -*- coding: utf-8 -*-
"""Intercompany rule: declarative mirror policy between two sister companies.

When Company A posts a sales invoice to a partner that represents Company B,
the rule auto-creates a draft vendor bill in Company B (and vice-versa for
vendor bills). This avoids the dual-entry burden of manual mirroring.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class IntercompanyRule(models.Model):
    _name = "account.intercompany.rule"
    _description = "Intercompany Mirror Rule"
    _order = "company_from_id, company_to_id"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)

    company_from_id = fields.Many2one(
        "res.company",
        string="Issuing Company",
        required=True,
        ondelete="cascade",
        help="The company that ORIGINATES the document (e.g. issues the sales invoice).",
    )
    company_to_id = fields.Many2one(
        "res.company",
        string="Receiving Company",
        required=True,
        ondelete="cascade",
        help="The sister company that RECEIVES the mirrored document.",
    )

    direction = fields.Selection(
        [
            ("sale_to_purchase", "Sales invoice in A → Vendor bill in B"),
            ("purchase_to_sale", "Vendor bill in A → Sales invoice in B"),
            ("both", "Both directions (most common)"),
        ],
        default="both",
        required=True,
    )

    target_journal_id = fields.Many2one(
        "account.journal",
        string="Target Journal",
        domain="[('company_id', '=', company_to_id), ('type', 'in', ('purchase', 'sale'))]",
        help="Journal used to post the mirrored move in the receiving company. "
        "If empty the default purchase/sale journal of the receiving company is used.",
    )

    auto_validate = fields.Boolean(
        default=False,
        help="If checked the mirrored move is posted automatically (instead of left in draft).",
    )

    # ---- Account mapping ----
    # Mapping pairs let two companies share a chart but have differing
    # account codes between them. Empty mapping = mirror uses the same
    # account if it exists in the receiving company (matched by code).
    mapping_ids = fields.One2many(
        "account.intercompany.account.mapping",
        "rule_id",
        string="Account Mapping",
        copy=True,
    )

    _unique_pair_direction = models.Constraint(
        "unique (company_from_id, company_to_id, direction)",
        "An intercompany rule for this company pair + direction already exists.",
    )

    @api.constrains("company_from_id", "company_to_id")
    def _check_distinct_companies(self):
        for rec in self:
            if rec.company_from_id == rec.company_to_id:
                raise ValidationError(_("Intercompany rule must point to a different company."))

    def _map_account(self, src_account):
        """Return the receiving-side account for ``src_account`` per mapping.

        Resolution order:
          1. Explicit mapping row.
          2. Same code in the receiving company.
          3. Empty (caller handles by skipping the line and logging a warning).
        """
        self.ensure_one()
        if not src_account:
            return self.env["account.account"]
        explicit = self.mapping_ids.filtered(lambda m: m.source_account_id == src_account)
        if explicit:
            return explicit[0].target_account_id
        return (
            self.env["account.account"]
            .sudo()
            .search(
                [("code", "=", src_account.code), ("company_ids", "in", self.company_to_id.id)],
                limit=1,
            )
        )
