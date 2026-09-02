# -*- coding: utf-8 -*-
"""Link an asset-loan rental.order back to the intercompany sales order that
spawned it, and tag whether it is the pre-flight or the event cycle."""

from __future__ import annotations

from odoo import fields, models


class RentalOrder(models.Model):
    _inherit = "rental.order"

    sale_order_id = fields.Many2one(
        "sale.order",
        string="Source Sales Order",
        index=True,
        copy=False,
        help="Intercompany sales order this asset loan was spawned from.",
    )
    loan_type = fields.Selection(
        [("preflight", "Pre-Flight Survey"), ("event", "Event")],
        string="Loan Cycle",
        copy=False,
        help="Pre-flight and event handovers share one SO but are separate physical pickup/return cycles.",
    )
