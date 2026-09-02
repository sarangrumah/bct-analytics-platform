# -*- coding: utf-8 -*-
"""Stock Summary Report — on-hand qty + value per SKU/warehouse/location.

Read-only SQL view over ``stock_quant`` (internal locations only). The
warehouse is resolved through the location parent_path prefix of each
warehouse's view location (stock.location has no stored warehouse_id).
Value = quantity × company-dependent ``standard_price``.
"""

from odoo import fields, models, tools


COST_SQL = "COALESCE((pp.standard_price ->> q.company_id::text)::float, 0.0)"


class WmsStockSummaryReport(models.Model):
    _name = "custom.wms.stock.summary.report"
    _inherit = ["custom.wms.xlsx.report"]
    _description = "Stock Summary Report (Qty + Value)"
    _auto = False
    _rec_name = "product_id"
    _order = "warehouse_id, location_id, default_code"
    _depends = {
        "stock.quant": [
            "location_id",
            "product_id",
            "lot_id",
            "quantity",
            "reserved_quantity",
            "in_date",
            "company_id",
        ],
        "product.product": ["default_code", "standard_price", "product_tmpl_id"],
    }

    warehouse_id = fields.Many2one("stock.warehouse", readonly=True)
    location_id = fields.Many2one("stock.location", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial", readonly=True)
    quantity = fields.Float(string="On Hand", readonly=True)
    reserved_quantity = fields.Float(string="Reserved", readonly=True)
    available_quantity = fields.Float(string="Available", readonly=True)
    unit_cost = fields.Float(readonly=True, aggregator="avg")
    value = fields.Float(string="Stock Value", readonly=True)
    in_date = fields.Datetime(string="Entry Date", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    q.id AS id,
                    wh.id AS warehouse_id,
                    q.location_id AS location_id,
                    q.product_id AS product_id,
                    pp.default_code AS default_code,
                    pt.categ_id AS categ_id,
                    q.lot_id AS lot_id,
                    q.quantity AS quantity,
                    q.reserved_quantity AS reserved_quantity,
                    q.quantity - q.reserved_quantity AS available_quantity,
                    {COST_SQL} AS unit_cost,
                    q.quantity * {COST_SQL} AS value,
                    q.in_date AS in_date,
                    q.company_id AS company_id
                FROM stock_quant q
                JOIN stock_location l ON l.id = q.location_id
                JOIN product_product pp ON pp.id = q.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN stock_warehouse wh
                    ON wh.company_id = q.company_id
                   AND l.parent_path LIKE (
                        SELECT wl.parent_path || '%'
                        FROM stock_location wl
                        WHERE wl.id = wh.view_location_id
                   )
                WHERE l.usage = 'internal'
            )
            """
        )

    # ------------------------------------------------------------------
    # XLSX (barcode) export
    # ------------------------------------------------------------------
    def _xlsx_title(self):
        return "WMS Stock Summary"

    def _xlsx_doc_barcode(self, rec):
        # There is no transaction behind a quant, so the "document" level of a
        # stock summary is the bin: that is what a counter scans to reach the
        # rows below it.
        return rec.location_id.barcode or rec.location_id.complete_name or ""

    def _xlsx_columns(self):
        return [
            {"label": "Warehouse", "value": lambda r: r.warehouse_id.display_name, "width": 22},
            {"label": "Location", "value": lambda r: r.location_id.complete_name, "width": 28},
            {"label": "SKU", "value": lambda r: r.default_code, "width": 14},
            {"label": "Product", "value": lambda r: r.product_id.display_name, "width": 34},
            {"label": "Category", "value": lambda r: r.categ_id.display_name, "width": 20},
            {"label": "Lot/Serial", "value": lambda r: r.lot_id.name, "width": 18},
            {"label": "On Hand", "value": lambda r: r.quantity, "type": "number", "width": 12, "total": True},
            {"label": "Reserved", "value": lambda r: r.reserved_quantity, "type": "number", "width": 12, "total": True},
            {
                "label": "Available",
                "value": lambda r: r.available_quantity,
                "type": "number",
                "width": 12,
                "total": True,
            },
            {"label": "Unit Cost", "value": lambda r: r.unit_cost, "type": "money", "width": 14},
            {"label": "Stock Value", "value": lambda r: r.value, "type": "money", "width": 16, "total": True},
            {"label": "Entry Date", "value": lambda r: r.in_date, "type": "datetime", "width": 17},
        ]
