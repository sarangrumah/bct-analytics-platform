# -*- coding: utf-8 -*-
"""Transfer Report — read-only SQL view over stock moves that belong to a
picking (receipts / deliveries / internal transfers, including the moves
materialised by the WMS Transfer Order engine). Grouped in the views by
operation type, source/destination and product.
"""

from odoo import fields, models, tools


TRANSFER_KIND = [
    ("incoming", "Receipt"),
    ("outgoing", "Delivery"),
    ("internal", "Internal Transfer"),
]

MOVE_STATE = [
    ("draft", "Draft"),
    ("waiting", "Waiting Another Operation"),
    ("confirmed", "Waiting"),
    ("partially_available", "Partially Available"),
    ("assigned", "Ready"),
    ("done", "Done"),
    ("cancel", "Cancelled"),
]


class WmsTransferReport(models.Model):
    _name = "custom.wms.transfer.report"
    _inherit = ["custom.wms.xlsx.report"]
    _description = "Transfer Report"
    _auto = False
    _rec_name = "reference"
    _order = "date desc, id desc"
    _depends = {
        "stock.move": [
            "date",
            "reference",
            "origin",
            "picking_id",
            "picking_type_id",
            "location_id",
            "location_dest_id",
            "product_id",
            "product_uom_qty",
            "quantity",
            "state",
            "company_id",
        ],
        "stock.picking": ["partner_id"],
        "product.product": ["default_code", "product_tmpl_id"],
    }

    date = fields.Datetime(readonly=True)
    reference = fields.Char(readonly=True)
    origin = fields.Char(string="Source Document", readonly=True)
    picking_id = fields.Many2one("stock.picking", readonly=True)
    picking_type_id = fields.Many2one("stock.picking.type", string="Operation Type", readonly=True)
    transfer_kind = fields.Selection(TRANSFER_KIND, string="Kind", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    location_id = fields.Many2one("stock.location", string="From", readonly=True)
    location_dest_id = fields.Many2one("stock.location", string="To", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    demand_qty = fields.Float(string="Demand", readonly=True)
    done_qty = fields.Float(string="Done", readonly=True)
    state = fields.Selection(MOVE_STATE, readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    m.id AS id,
                    m.date AS date,
                    m.reference AS reference,
                    m.origin AS origin,
                    m.picking_id AS picking_id,
                    m.picking_type_id AS picking_type_id,
                    spt.code AS transfer_kind,
                    pick.partner_id AS partner_id,
                    m.location_id AS location_id,
                    m.location_dest_id AS location_dest_id,
                    m.product_id AS product_id,
                    pp.default_code AS default_code,
                    pt.categ_id AS categ_id,
                    m.product_uom_qty AS demand_qty,
                    CASE WHEN m.state = 'done' THEN m.quantity ELSE 0.0 END AS done_qty,
                    m.state AS state,
                    m.company_id AS company_id
                FROM stock_move m
                JOIN stock_picking_type spt ON spt.id = m.picking_type_id
                JOIN product_product pp ON pp.id = m.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN stock_picking pick ON pick.id = m.picking_id
                WHERE m.state != 'cancel'
            )
            """
        )

    # ------------------------------------------------------------------
    # XLSX (barcode) export
    # ------------------------------------------------------------------
    def _xlsx_title(self):
        return "WMS Transfer Report"

    def _xlsx_doc_barcode(self, rec):
        return rec.picking_id.name or rec.reference or ""

    def _xlsx_columns(self):
        return [
            {"label": "Date", "value": lambda r: r.date, "type": "datetime", "width": 17},
            {"label": "Reference", "value": lambda r: r.reference, "width": 20},
            {"label": "Source Document", "value": lambda r: r.origin, "width": 18},
            {"label": "Operation Type", "value": lambda r: r.picking_type_id.display_name, "width": 24},
            {
                "label": "Kind",
                "value": lambda r: dict(TRANSFER_KIND).get(r.transfer_kind, r.transfer_kind),
                "width": 15,
            },
            {"label": "Partner", "value": lambda r: r.partner_id.display_name, "width": 28},
            {"label": "From", "value": lambda r: r.location_id.complete_name, "width": 26},
            {"label": "To", "value": lambda r: r.location_dest_id.complete_name, "width": 26},
            {"label": "SKU", "value": lambda r: r.default_code, "width": 14},
            {"label": "Product", "value": lambda r: r.product_id.display_name, "width": 34},
            {"label": "Demand", "value": lambda r: r.demand_qty, "type": "number", "width": 12, "total": True},
            {"label": "Done", "value": lambda r: r.done_qty, "type": "number", "width": 12, "total": True},
            {"label": "Status", "value": lambda r: dict(MOVE_STATE).get(r.state, r.state), "width": 16},
        ]
