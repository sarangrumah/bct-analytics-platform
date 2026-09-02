# -*- coding: utf-8 -*-
from odoo import fields, models

# Sequence code used for the confirmed Sales Order number. The draft/quotation
# number keeps using the standard ``sale.order`` sequence code (which this
# tenant points at an ``SQ/<CODE>/...`` prefix), so quotations read SQ/... and
# become SO/... only on confirmation.
SO_SEQUENCE_CODE = "tenant.sale_order"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_quotation_name = fields.Char(
        string="Quotation Number",
        copy=False,
        readonly=True,
        help="Original SQ/... number this order carried while it was a "
        "quotation. Kept for audit after it is re-numbered to SO/... on "
        "confirmation.",
    )

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            code = order.company_id.x_doc_code
            # Inert for companies without a document code, or if somehow not
            # confirmed, or if already carrying a Sales Order number.
            if not code or order.state != "sale":
                continue
            if order.name and order.name.startswith("SO/"):
                continue
            new_name = (
                self.env["ir.sequence"]
                .with_company(order.company_id)
                .next_by_code(SO_SEQUENCE_CODE, sequence_date=order.date_order)
            )
            if not new_name:
                continue
            if not order.x_quotation_name:
                order.x_quotation_name = order.name
            order.name = new_name
        return res
