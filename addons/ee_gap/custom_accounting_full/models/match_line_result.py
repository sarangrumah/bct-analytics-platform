# -*- coding: utf-8 -*-
"""Per-line 3-way match outcome."""

from __future__ import annotations

from odoo import fields, models


class MatchLineResult(models.Model):
    _name = "custom.match.line.result"
    _description = "3-Way Match Line Result"
    _order = "result_id, id"

    result_id = fields.Many2one(
        "custom.match.result",
        required=True,
        ondelete="cascade",
    )
    bill_line_id = fields.Many2one("account.move.line")
    po_line_id = fields.Many2one("purchase.order.line")

    ordered_qty = fields.Float()
    received_qty = fields.Float()
    billed_qty = fields.Float()
    qty_variance_pct = fields.Float()

    unit_price_po = fields.Float()
    unit_price_bill = fields.Float()
    price_variance_pct = fields.Float()
    # Spec-aligned aliases
    ordered_price = fields.Float(related="unit_price_po", store=False, string="Unit Price PO (Spec)")
    billed_price = fields.Float(related="unit_price_bill", store=False, string="Unit Price Bill (Spec)")
    computed_at = fields.Datetime(related="result_id.computed_at", store=False)

    status = fields.Selection(
        [
            ("pass", "Pass"),
            ("qty_variance", "Qty Variance"),
            ("price_variance", "Price Variance"),
            ("both", "Both"),
        ],
        default="pass",
    )
