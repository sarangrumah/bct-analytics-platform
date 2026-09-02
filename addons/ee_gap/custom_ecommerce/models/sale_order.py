# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_awb_number = fields.Char(string="AWB / Resi No")
    x_awb_tracking_url = fields.Char(
        string="AWB Tracking URL",
        compute="_compute_awb_url",
        store=True,
    )
    x_id_courier_id = fields.Many2one(
        "custom.ecommerce.courier",
        string="Indonesian Courier",
        related="carrier_id.x_id_courier_id",
        store=True,
        readonly=True,
    )

    @api.depends(
        "x_awb_number",
        "carrier_id",
        "carrier_id.x_id_courier_id",
        "carrier_id.x_id_courier_id.tracking_url_template",
    )
    def _compute_awb_url(self):
        for rec in self:
            template = (
                rec.carrier_id.x_id_courier_id.tracking_url_template
                if rec.carrier_id and rec.carrier_id.x_id_courier_id
                else False
            )
            if template and rec.x_awb_number:
                try:
                    rec.x_awb_tracking_url = template.format(awb=rec.x_awb_number)
                except (KeyError, IndexError, ValueError):
                    rec.x_awb_tracking_url = False
            else:
                rec.x_awb_tracking_url = False
