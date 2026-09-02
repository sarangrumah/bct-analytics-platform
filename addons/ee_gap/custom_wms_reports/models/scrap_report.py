# -*- coding: utf-8 -*-
"""Scrap Report — read-only SQL view over ``stock_scrap``.

One row per scrap order (the model is already one product per record in
Odoo 19). Value falls back to the company-dependent ``standard_price``,
which is the same basis the stock summary and purchase-return reports use,
so the three tie out against each other.

``should_replenish`` is surfaced because a replenished scrap creates demand
downstream — the warehouse needs to see which write-offs it must re-buy.
"""

from odoo import fields, models, tools


COST_SQL = "COALESCE((pp.standard_price ->> sc.company_id::text)::float, 0.0)"

SCRAP_STATE = [
    ("draft", "Draft"),
    ("confirmed", "Confirmed"),
    ("done", "Done"),
    ("cancel", "Cancelled"),
]


class WmsScrapReport(models.Model):
    _name = "custom.wms.scrap.report"
    _inherit = ["custom.wms.xlsx.report"]
    _description = "Scrap Report"
    _auto = False
    _rec_name = "name"
    _order = "date_done desc, id desc"
    # An _auto=False model only flushes itself, so a scrap validated earlier in
    # the same transaction would still read as draft through the view. Naming
    # the underlying models makes the ORM flush them before the query.
    _depends = {
        "stock.scrap": [
            "name",
            "date_done",
            "origin",
            "picking_id",
            "product_id",
            "lot_id",
            "location_id",
            "scrap_location_id",
            "scrap_qty",
            "should_replenish",
            "state",
            "company_id",
        ],
        "product.product": ["default_code", "standard_price", "product_tmpl_id"],
    }

    name = fields.Char(string="Reference", readonly=True)
    date_done = fields.Datetime(string="Scrap Date", readonly=True)
    origin = fields.Char(string="Source Document", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transfer", readonly=True)
    scrap_id = fields.Many2one("stock.scrap", string="Scrap Order", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial", readonly=True)
    location_id = fields.Many2one("stock.location", string="From Location", readonly=True)
    scrap_location_id = fields.Many2one("stock.location", string="Scrap Location", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", readonly=True)
    scrap_qty = fields.Float(string="Scrapped Qty", readonly=True)
    unit_cost = fields.Float(readonly=True, aggregator="avg")
    scrap_value = fields.Float(string="Scrap Value", readonly=True)
    should_replenish = fields.Boolean(string="Replenish", readonly=True)
    state = fields.Selection(SCRAP_STATE, readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    sc.id AS id,
                    sc.id AS scrap_id,
                    sc.name AS name,
                    sc.date_done AS date_done,
                    sc.origin AS origin,
                    sc.picking_id AS picking_id,
                    sc.product_id AS product_id,
                    pp.default_code AS default_code,
                    pt.categ_id AS categ_id,
                    sc.lot_id AS lot_id,
                    sc.location_id AS location_id,
                    sc.scrap_location_id AS scrap_location_id,
                    wh.id AS warehouse_id,
                    sc.scrap_qty AS scrap_qty,
                    {COST_SQL} AS unit_cost,
                    sc.scrap_qty * {COST_SQL} AS scrap_value,
                    sc.should_replenish AS should_replenish,
                    sc.state AS state,
                    sc.company_id AS company_id
                FROM stock_scrap sc
                JOIN product_product pp ON pp.id = sc.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                JOIN stock_location l ON l.id = sc.location_id
                -- Same warehouse resolution as the stock summary: match the
                -- source location against each warehouse view-location prefix.
                LEFT JOIN stock_warehouse wh
                    ON wh.company_id = sc.company_id
                   AND l.parent_path LIKE (
                        SELECT wl.parent_path || '%'
                        FROM stock_location wl
                        WHERE wl.id = wh.view_location_id
                   )
                WHERE sc.state != 'cancel'
            )
            """
        )

    # ------------------------------------------------------------------
    # XLSX (barcode) export
    # ------------------------------------------------------------------
    def _xlsx_title(self):
        return "WMS Scrap Report"

    def _xlsx_columns(self):
        return [
            {"label": "Scrap Ref", "value": lambda r: r.name, "width": 18},
            {"label": "Date", "value": lambda r: r.date_done, "type": "datetime", "width": 17},
            {"label": "SKU", "value": lambda r: r.default_code, "width": 14},
            {"label": "Product", "value": lambda r: r.product_id.display_name, "width": 34},
            {"label": "Lot/Serial", "value": lambda r: r.lot_id.name, "width": 16},
            {"label": "From", "value": lambda r: r.location_id.complete_name, "width": 26},
            {"label": "Scrap Location", "value": lambda r: r.scrap_location_id.complete_name, "width": 26},
            {"label": "Qty", "value": lambda r: r.scrap_qty, "type": "number", "width": 12, "total": True},
            {"label": "Unit Cost", "value": lambda r: r.unit_cost, "type": "money", "width": 14},
            {"label": "Value", "value": lambda r: r.scrap_value, "type": "money", "width": 16, "total": True},
            {"label": "Replenish", "value": lambda r: "Yes" if r.should_replenish else "No", "width": 11},
            {"label": "Status", "value": lambda r: dict(SCRAP_STATE).get(r.state, r.state), "width": 12},
        ]
