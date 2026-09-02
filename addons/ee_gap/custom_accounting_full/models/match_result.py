# -*- coding: utf-8 -*-
"""Match results recorded against vendor bills."""

from __future__ import annotations

from odoo import fields, models


class MatchResult(models.Model):
    _name = "custom.match.result"
    _description = "3-Way Match Result"
    _inherit = ["pdp.audited.mixin"]
    _order = "computed_at desc"

    move_id = fields.Many2one(
        "account.move",
        required=True,
        ondelete="cascade",
        index=True,
    )
    overall_status = fields.Selection(
        [
            ("pass", "Pass"),
            ("match", "Match"),
            ("qty_variance", "Quantity Variance"),
            ("qty_mismatch", "Quantity Mismatch"),
            ("price_variance", "Price Variance"),
            ("price_mismatch", "Price Mismatch"),
            ("both", "Qty + Price Variance"),
            ("both_mismatch", "Both Mismatch"),
            ("no_po", "No PO"),
            ("error", "Error"),
        ],
        default="pass",
    )
    computed_at = fields.Datetime(default=fields.Datetime.now)
    line_results = fields.One2many(
        "custom.match.line.result",
        "result_id",
        string="Line Results",
    )
    notes = fields.Text()

    def _pdp_audit_classification(self):
        return "financial"
