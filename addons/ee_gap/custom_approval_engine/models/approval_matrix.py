# -*- coding: utf-8 -*-
"""Approval matrix — declares which records of a model require approval.

A matrix is resolved against an arbitrary record by:
  1. Filtering matrices where ``model_id`` matches the record's ``_name``.
  2. Filtering by ``condition_domain`` (evaluated against the record).
  3. Picking the matrix with the highest ``priority`` among matches.

Each matrix has ordered tiers (``approval.matrix.tier``). An approval
request walks the tiers in sequence; only when one tier resolves does
the next become eligible.
"""

from __future__ import annotations

import ast
import logging
from typing import Optional

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ApprovalMatrix(models.Model):
    _name = "approval.matrix"
    _description = "Approval Matrix"
    _order = "priority desc, sequence asc, id asc"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    priority = fields.Integer(
        default=10,
        help="Higher value wins when multiple matrices match a record. "
        "Use to layer specific overrides on top of broader defaults.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, ondelete="cascade")

    model_id = fields.Many2one(
        "ir.model",
        string="Apply To Model",
        required=True,
        ondelete="cascade",
        domain="[('transient', '=', False)]",
    )
    model_name = fields.Char(related="model_id.model", store=True, string="Model Name", index=True)

    condition_domain = fields.Char(
        string="Filter Domain",
        default="[]",
        help="Odoo domain evaluated against the candidate record. Empty list = always applies.",
    )

    trigger = fields.Selection(
        [
            ("manual", "Manual (user requests)"),
            ("on_create", "On record create"),
            ("on_state_change", "On state change"),
        ],
        default="manual",
        required=True,
    )

    tier_ids = fields.One2many("approval.matrix.tier", "matrix_id", string="Tiers", copy=True)
    tier_count = fields.Integer(compute="_compute_tier_count")

    notes = fields.Text()

    # ---- Computed ----

    @api.depends("tier_ids")
    def _compute_tier_count(self):
        for rec in self:
            rec.tier_count = len(rec.tier_ids)

    # ---- Constraints ----

    @api.constrains("condition_domain")
    def _check_domain(self):
        for rec in self:
            domain = rec.condition_domain or "[]"
            try:
                parsed = ast.literal_eval(domain)
                if not isinstance(parsed, list):
                    raise ValueError("Domain must be a list literal")
            except (SyntaxError, ValueError) as e:
                raise ValidationError(
                    _("Invalid condition_domain on matrix '%(name)s': %(err)s", name=rec.name, err=e)
                ) from e

    @api.constrains("tier_ids")
    def _check_has_tiers(self):
        for rec in self:
            if rec.active and not rec.tier_ids:
                raise ValidationError(_("Matrix '%s' is active but has no tiers. Add at least one.") % rec.name)

    # ---- Resolution ----

    @api.model
    def _domain_matches(self, domain_str: str, record) -> bool:
        try:
            domain = ast.literal_eval(domain_str or "[]")
        except (SyntaxError, ValueError):
            return False
        if not domain:
            return True
        Model = self.env[record._name]
        return bool(Model.search_count([("id", "=", record.id), *domain]))

    @api.model
    def _resolve_for(self, record) -> Optional["ApprovalMatrix"]:
        """Return the highest-priority active matrix that matches ``record``, or None."""
        candidates = self.sudo().search(
            [
                ("active", "=", True),
                ("model_name", "=", record._name),
                ("company_id", "in", [False, record.company_id.id if hasattr(record, "company_id") else False]),
            ],
            order="priority desc, sequence asc",
        )
        for m in candidates:
            if m._domain_matches(m.condition_domain, record):
                return m
        return None
