# -*- coding: utf-8 -*-
from odoo import fields, models, tools


class CustomRentalSchedule(models.Model):
    """Read-only SQL view aggregating rental.order into a calendar-friendly
    schedule. One row per rental.order (we extend this if multi-line rental
    is later introduced)."""

    _name = "custom.rental.schedule"
    _description = "Rental Schedule (SQL View)"
    _auto = False
    _order = "date_start"

    name = fields.Char(readonly=True)
    order_id = fields.Many2one("rental.order", readonly=True)
    line_id = fields.Many2one(
        "rental.order", readonly=True, help="Alias of order_id; reserved for future multi-line rental support."
    )
    product_id = fields.Many2one("product.product", readonly=True)
    asset_id = fields.Many2one("rental.asset", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    date_start = fields.Datetime(readonly=True)
    date_stop = fields.Datetime(readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("picked_up", "Picked Up"),
            ("returned", "Returned"),
            ("cancelled", "Cancelled"),
            ("late", "Late"),
        ],
        readonly=True,
    )

    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute("""
            CREATE OR REPLACE VIEW custom_rental_schedule AS (
                SELECT
                    ro.id                              AS id,
                    ro.id                              AS order_id,
                    ro.id                              AS line_id,
                    ro.name                            AS name,
                    ra.product_id                      AS product_id,
                    ro.asset_id                        AS asset_id,
                    ro.partner_id                      AS partner_id,
                    ro.company_id                      AS company_id,
                    ro.pickup_dt                       AS date_start,
                    ro.return_dt_expected              AS date_stop,
                    CASE
                        WHEN ro.state = 'picked_up'
                             AND ro.return_dt_expected < (NOW() AT TIME ZONE 'UTC')
                        THEN 'late'
                        ELSE ro.state
                    END                                AS state
                FROM rental_order ro
                LEFT JOIN rental_asset ra ON ra.id = ro.asset_id
                WHERE ro.pickup_dt IS NOT NULL
                  AND ro.return_dt_expected IS NOT NULL
            )
        """)
