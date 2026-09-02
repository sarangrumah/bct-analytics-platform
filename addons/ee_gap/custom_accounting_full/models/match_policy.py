# -*- coding: utf-8 -*-
"""3-way match policy + result models + action_post hook."""

from __future__ import annotations

from odoo import fields, models


class MatchPolicy(models.Model):
    _name = "custom.match.policy"
    _description = "3-Way Match Policy"
    _order = "company_id, id"

    name = fields.Char(required=True, default="Default")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    qty_tolerance_percent = fields.Float(
        string="Qty Tolerance (%)",
        default=5.0,
    )
    price_tolerance_percent = fields.Float(
        string="Price Tolerance (%)",
        default=2.0,
    )
    on_qty_mismatch = fields.Selection(
        [("warn", "Warn"), ("block", "Block")],
        default="warn",
        required=True,
    )
    on_price_mismatch = fields.Selection(
        [("warn", "Warn"), ("block", "Block")],
        default="warn",
        required=True,
    )
    applies_to_purchase_categ_ids = fields.Many2many(
        "product.category",
        "custom_match_policy_categ_rel",
        "policy_id",
        "categ_id",
        string="Limit To Categories",
        help="If set, the policy only applies when at least one bill line's product is in one of these categories.",
    )

    _unique_active_per_company = models.Constraint(
        "UNIQUE(company_id, name)",
        "Policy name must be unique per company.",
    )
