# -*- coding: utf-8 -*-
from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # Re-declares the EXACT core @api.depends list (verified against
    # sale/models/sale_order_line.py@19.0) PLUS the four order fields the event
    # block is built from, so editing the event data refreshes the descriptions
    # that were derived from it. The field stays readonly=False, so a manually
    # typed description survives until one of these dependencies changes.
    @api.depends(
        "product_id",
        "linked_line_id",
        "linked_line_ids",
        "order_id.x_custom_event_name",
        "order_id.x_custom_event_location",
        "order_id.x_custom_show_date",
        "order_id.x_custom_dp_note",
    )
    def _compute_name(self):
        super()._compute_name()
        for line in self:
            # Down-payment lines carry their own wording ("Down Payment (ref:
            # …)"), which the client reconciles against — leave them alone.
            if not line.product_id or line.is_downpayment:
                continue
            event = line.order_id._custom_event_description()
            if not event:
                continue
            # super() has just rebuilt `name` from the product, so the first
            # line is the product description and the block below is ours.
            line.name = "%s\n%s" % ((line.name or "").strip(), event)
