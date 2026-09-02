# -*- coding: utf-8 -*-
"""Stock Take / Spot Check Report — read-only SQL view over cycle-count
lines, enriched with the plan's sampling method (a ``spot_check`` line is
just a line whose plan method is ``spot_check``) and a variance value from
the company-dependent ``standard_price``.
"""

from odoo import fields, models, tools


COST_SQL = "COALESCE((pp.standard_price ->> s.company_id::text)::float, 0.0)"

# Mirrors custom_wms_cycle_count METHOD + our spot_check extension; kept as a
# plain copy because selection fields on SQL views cannot follow selection_add.
METHOD = [
    ("abc_velocity", "ABC Velocity"),
    ("random", "Random"),
    ("by_zone", "By Zone"),
    ("by_value", "By Value"),
    ("last_counted", "Last Counted"),
    ("spot_check", "Spot Check"),
]

STATUS = [
    ("pending", "Pending"),
    ("counted", "Counted"),
    ("skipped", "Skipped"),
    ("recount_required", "Recount Required"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
]


class WmsStockTakeReport(models.Model):
    _name = "custom.wms.stock.take.report"
    _inherit = ["custom.wms.xlsx.report"]
    _description = "Stock Take Report"
    _auto = False
    _rec_name = "session_id"
    _order = "counted_at desc, id desc"
    _depends = {
        "custom.cycle.count.line": [
            "session_id",
            "counted_at",
            "counter_user_id",
            "location_id",
            "product_id",
            "lot_id",
            "status",
            "expected_qty",
            "counted_qty",
            "variance_qty",
            "variance_pct",
        ],
        "custom.cycle.count.session": ["plan_id", "state", "warehouse_id", "scheduled_date", "company_id"],
        "product.product": ["default_code", "standard_price", "product_tmpl_id"],
    }

    session_id = fields.Many2one("custom.cycle.count.session", readonly=True)
    plan_id = fields.Many2one("custom.cycle.count.plan", readonly=True)
    method = fields.Selection(METHOD, string="Count Method", readonly=True)
    session_state = fields.Char(readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", readonly=True)
    scheduled_date = fields.Date(readonly=True)
    counted_at = fields.Datetime(readonly=True)
    counter_user_id = fields.Many2one("res.users", string="Counter", readonly=True)
    location_id = fields.Many2one("stock.location", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial", readonly=True)
    status = fields.Selection(STATUS, readonly=True)
    expected_qty = fields.Float(string="System Qty", readonly=True)
    counted_qty = fields.Float(string="Counted Qty", readonly=True)
    variance_qty = fields.Float(readonly=True)
    variance_pct = fields.Float(string="Variance %", readonly=True, aggregator="avg")
    unit_cost = fields.Float(readonly=True, aggregator="avg")
    variance_value = fields.Float(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    ln.id AS id,
                    ln.session_id AS session_id,
                    s.plan_id AS plan_id,
                    p.method AS method,
                    s.state AS session_state,
                    s.warehouse_id AS warehouse_id,
                    s.scheduled_date AS scheduled_date,
                    ln.counted_at AS counted_at,
                    ln.counter_user_id AS counter_user_id,
                    ln.location_id AS location_id,
                    ln.product_id AS product_id,
                    pp.default_code AS default_code,
                    pt.categ_id AS categ_id,
                    ln.lot_id AS lot_id,
                    ln.status AS status,
                    ln.expected_qty AS expected_qty,
                    ln.counted_qty AS counted_qty,
                    ln.variance_qty AS variance_qty,
                    ln.variance_pct AS variance_pct,
                    {COST_SQL} AS unit_cost,
                    ln.variance_qty * {COST_SQL} AS variance_value,
                    s.company_id AS company_id
                FROM custom_cycle_count_line ln
                JOIN custom_cycle_count_session s ON s.id = ln.session_id
                LEFT JOIN custom_cycle_count_plan p ON p.id = s.plan_id
                JOIN product_product pp ON pp.id = ln.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
            )
            """
        )

    # ------------------------------------------------------------------
    # XLSX (barcode) export
    # ------------------------------------------------------------------
    def _xlsx_title(self):
        return "WMS Stock Take Report"

    def _xlsx_doc_barcode(self, rec):
        return rec.session_id.name or ""

    def _xlsx_columns(self):
        return [
            {"label": "Session", "value": lambda r: r.session_id.display_name, "width": 22},
            {"label": "Plan", "value": lambda r: r.plan_id.display_name, "width": 24},
            {"label": "Method", "value": lambda r: dict(METHOD).get(r.method, r.method), "width": 16},
            {"label": "Warehouse", "value": lambda r: r.warehouse_id.display_name, "width": 22},
            {"label": "Scheduled", "value": lambda r: r.scheduled_date, "type": "date", "width": 13},
            {"label": "Counted At", "value": lambda r: r.counted_at, "type": "datetime", "width": 17},
            {"label": "Counter", "value": lambda r: r.counter_user_id.display_name, "width": 20},
            {"label": "Location", "value": lambda r: r.location_id.complete_name, "width": 28},
            {"label": "SKU", "value": lambda r: r.default_code, "width": 14},
            {"label": "Product", "value": lambda r: r.product_id.display_name, "width": 34},
            {"label": "Lot/Serial", "value": lambda r: r.lot_id.name, "width": 18},
            {"label": "System Qty", "value": lambda r: r.expected_qty, "type": "number", "width": 12, "total": True},
            {"label": "Counted Qty", "value": lambda r: r.counted_qty, "type": "number", "width": 12, "total": True},
            {"label": "Variance", "value": lambda r: r.variance_qty, "type": "number", "width": 12, "total": True},
            {"label": "Variance %", "value": lambda r: r.variance_pct, "type": "money", "width": 12},
            {"label": "Unit Cost", "value": lambda r: r.unit_cost, "type": "money", "width": 14},
            {
                "label": "Variance Value",
                "value": lambda r: r.variance_value,
                "type": "money",
                "width": 16,
                "total": True,
            },
            {"label": "Status", "value": lambda r: dict(STATUS).get(r.status, r.status), "width": 16},
        ]
