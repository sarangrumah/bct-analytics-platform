# -*- coding: utf-8 -*-
"""Extend analytic accounts with a branch dimension for kantor-cabang reporting."""

from __future__ import annotations

from odoo import api, fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"

    x_custom_branch_code = fields.Char(
        string="Branch Code",
        help="Internal branch / kantor cabang code (e.g. JKT-01, SBY-02).",
    )
    x_custom_is_branch_root = fields.Boolean(
        string="Branch Root",
        default=False,
        help="When True, this analytic account is the root for a legal branch and "
        "all child analytic accounts are considered part of that branch's books.",
    )
    x_custom_parent_id = fields.Many2one(
        "account.analytic.account",
        string="Parent Analytic Account",
        index=True,
        help="Parent analytic account for branch hierarchy. Replaces the native "
        "parent_id that was removed in Odoo 19 (hierarchy now lives on plan).",
    )
    x_custom_branch_root_id = fields.Many2one(
        "account.analytic.account",
        string="Branch Root (Computed)",
        compute="_compute_branch_root",
        store=True,
        recursive=True,
    )

    @api.depends("x_custom_parent_id", "x_custom_parent_id.x_custom_branch_root_id", "x_custom_is_branch_root")
    def _compute_branch_root(self):
        for rec in self:
            if rec.x_custom_is_branch_root:
                rec.x_custom_branch_root_id = rec
            elif rec.x_custom_parent_id:
                rec.x_custom_branch_root_id = rec.x_custom_parent_id.x_custom_branch_root_id
            else:
                rec.x_custom_branch_root_id = False
