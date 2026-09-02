# -*- coding: utf-8 -*-
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CustomBarcodeScanLine(models.Model):
    _name = "custom.barcode.scan.line"
    _description = "Barcode Scan Line"
    _order = "scanned_at desc, id desc"

    session_id = fields.Many2one(
        "custom.barcode.scan.session",
        string="Scan Session",
        ondelete="cascade",
        index=True,
    )
    batch_session_id = fields.Many2one(
        "custom.barcode.batch.session",
        string="Batch Session",
        ondelete="cascade",
        index=True,
    )
    cluster_run_id = fields.Many2one(
        "custom.barcode.cluster.run",
        string="Cluster Run",
        ondelete="cascade",
        index=True,
    )
    picking_id = fields.Many2one(
        "stock.picking",
        string="Allocated To",
        help="Set when this line was distributed/allocated to a specific picking by a batch or cluster session.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
    )
    # Odoo 19: stock_production_lot was renamed to stock.lot
    lot_id = fields.Many2one(
        "stock.lot",
        string="Lot / Serial",
    )
    raw_barcode = fields.Char(string="Raw Barcode")
    quantity = fields.Float(default=1.0)
    scanned_at = fields.Datetime(
        string="Scanned At",
        default=fields.Datetime.now,
    )
    status = fields.Selection(
        [
            ("ok", "OK"),
            ("not_found", "Not Found"),
            ("duplicate", "Duplicate"),
            ("wrong_location", "Wrong Location"),
            ("unallocated", "Unallocated"),
        ],
        default="ok",
        required=True,
    )
    # GS1 parse result, stored as JSON text for portability (Json field needs PG JSONB).
    x_gs1_parsed = fields.Text(
        string="GS1 Parsed",
        help="JSON-encoded dict of parsed GS1 Application Identifiers "
        "(e.g. {'gtin': '...', 'lot': '...', 'exp_date': '2026-01-31', 'weight': 1.25}).",
    )

    @api.constrains("session_id", "batch_session_id", "cluster_run_id")
    def _check_owner(self):
        for rec in self:
            owners = [rec.session_id, rec.batch_session_id, rec.cluster_run_id]
            if not any(owners):
                raise ValidationError("Scan line must belong to a session, batch or cluster run.")

    def get_gs1_dict(self):
        """Return parsed GS1 payload as a Python dict (or {} if absent/invalid)."""
        self.ensure_one()
        if not self.x_gs1_parsed:
            return {}
        try:
            return json.loads(self.x_gs1_parsed)
        except (ValueError, TypeError):
            return {}
