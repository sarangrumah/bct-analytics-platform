# -*- coding: utf-8 -*-
"""Provenance of a received quantity: which scan session filled this line.

Needed to tell a *pre-filled* move line (Odoo 19 stamps the full demand on
an incoming line at confirm) apart from one an operator actually scanned.
Without it, applying a scan session either stacks on the demand (over-receipt)
or wipes what a previous session on the same receipt already booked.
"""

from __future__ import annotations

from odoo import fields, models


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    wms_scan_session_id = fields.Many2one(
        "custom.barcode.scan.session",
        string="Filled by Scan Session",
        readonly=True,
        copy=False,
        index=True,
        help="Set when the quantity on this line came from a barcode scan "
        "session rather than from the demand pre-fill.",
    )
