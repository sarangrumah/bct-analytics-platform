# -*- coding: utf-8 -*-
"""Purchase Return Report — read-only SQL view.

A "purchase return" is any *done* stock move whose destination is a
supplier location (the classic return-to-vendor flow, whether created via
the Return wizard or a manual RTV picking). Value falls back through:
move valuation ``value`` → move ``price_unit`` → the product's
company-dependent ``standard_price`` (JSONB, keyed by company id).
"""

from odoo import fields, models, tools


# Company-dependent standard_price lives in a JSONB column keyed by company
# id; there is no per-DB fallback key, so missing company entries read as 0.
COST_SQL = "COALESCE((pp.standard_price ->> m.company_id::text)::float, 0.0)"


class WmsPurchaseReturnReport(models.Model):
    _name = "custom.wms.purchase.return.report"
    _inherit = ["custom.wms.xlsx.report"]
    _description = "Purchase Return Report"
    _auto = False
    _rec_name = "reference"
    _order = "date desc, id desc"
    _depends = {
        "stock.move": [
            "date",
            "reference",
            "origin",
            "picking_id",
            "product_id",
            "quantity",
            "price_unit",
            "value",
            "origin_returned_move_id",
            "location_dest_id",
            "state",
            "company_id",
        ],
        "stock.picking": ["partner_id"],
        "product.product": ["default_code", "standard_price", "product_tmpl_id"],
    }

    date = fields.Datetime(readonly=True)
    reference = fields.Char(readonly=True)
    origin = fields.Char(string="Source Document", readonly=True)
    picking_id = fields.Many2one("stock.picking", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Supplier", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    default_code = fields.Char(string="SKU", readonly=True)
    categ_id = fields.Many2one("product.category", string="Category", readonly=True)
    lot_ids_display = fields.Char(string="Lots/Serials", readonly=True)
    quantity = fields.Float(string="Returned Qty", readonly=True)
    unit_cost = fields.Float(readonly=True, aggregator="avg")
    value = fields.Float(string="Return Value", readonly=True)
    is_return_of_receipt = fields.Boolean(
        string="Linked to Receipt",
        readonly=True,
        help="True when the move was created by the Return wizard from an "
        "original receipt (origin_returned_move_id is set).",
    )
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
                    pick.partner_id AS partner_id,
                    m.product_id AS product_id,
                    pp.default_code AS default_code,
                    pt.categ_id AS categ_id,
                    (
                        SELECT string_agg(DISTINCT sl.name, ', ')
                        FROM stock_move_line sml
                        JOIN stock_lot sl ON sl.id = sml.lot_id
                        WHERE sml.move_id = m.id
                    ) AS lot_ids_display,
                    m.quantity AS quantity,
                    COALESCE(
                        NULLIF(ABS(m.value) / NULLIF(m.quantity, 0), 0),
                        NULLIF(ABS(m.price_unit), 0),
                        {COST_SQL}
                    ) AS unit_cost,
                    m.quantity * COALESCE(
                        NULLIF(ABS(m.value) / NULLIF(m.quantity, 0), 0),
                        NULLIF(ABS(m.price_unit), 0),
                        {COST_SQL}
                    ) AS value,
                    (m.origin_returned_move_id IS NOT NULL) AS is_return_of_receipt,
                    m.company_id AS company_id
                FROM stock_move m
                JOIN stock_location dest ON dest.id = m.location_dest_id
                JOIN product_product pp ON pp.id = m.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN stock_picking pick ON pick.id = m.picking_id
                WHERE m.state = 'done'
                  AND dest.usage = 'supplier'
            )
            """
        )

    # ------------------------------------------------------------------
    # XLSX (barcode) export
    # ------------------------------------------------------------------
    def _xlsx_title(self):
        return "WMS Purchase Return Report"

    def _xlsx_doc_barcode(self, rec):
        return rec.picking_id.name or rec.reference or ""

    def _xlsx_line_barcode(self, rec):
        # A return line is keyed on the SKU: the lots are aggregated into a
        # display string by the view, so there is no single lot to scan.
        return rec.product_id.barcode or rec.default_code or ""

    def _xlsx_columns(self):
        return [
            {"label": "Date", "value": lambda r: r.date, "type": "datetime", "width": 17},
            {"label": "Reference", "value": lambda r: r.reference, "width": 20},
            {"label": "Source Document", "value": lambda r: r.origin, "width": 18},
            {"label": "Supplier", "value": lambda r: r.partner_id.display_name, "width": 30},
            {"label": "SKU", "value": lambda r: r.default_code, "width": 14},
            {"label": "Product", "value": lambda r: r.product_id.display_name, "width": 34},
            {"label": "Category", "value": lambda r: r.categ_id.display_name, "width": 20},
            {"label": "Lots/Serials", "value": lambda r: r.lot_ids_display, "width": 22},
            {"label": "Returned Qty", "value": lambda r: r.quantity, "type": "number", "width": 13, "total": True},
            {"label": "Unit Cost", "value": lambda r: r.unit_cost, "type": "money", "width": 14},
            {"label": "Return Value", "value": lambda r: r.value, "type": "money", "width": 16, "total": True},
            {
                "label": "Linked to Receipt",
                "value": lambda r: "Yes" if r.is_return_of_receipt else "No",
                "width": 15,
            },
        ]
