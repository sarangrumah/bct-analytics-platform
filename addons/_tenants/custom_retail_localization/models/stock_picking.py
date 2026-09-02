# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # Trade / Non-Trade stream of the source PO (feature #9); read by the GR
    # valuation poster to route the GR/IR clearing account.
    l10n_purchase_type = fields.Selection(
        [("trade", "Trade"), ("non_trade", "Non-Trade")],
        string="Purchase Type",
        compute="_compute_l10n_purchase_type",
        store=True,
    )

    @api.depends("purchase_id.l10n_purchase_type", "move_ids.purchase_line_id")
    def _compute_l10n_purchase_type(self):
        for picking in self:
            orders = picking._retail_purchase_orders()
            picking.l10n_purchase_type = orders[:1].l10n_purchase_type or False

    def _retail_purchase_orders(self):
        """Purchase order(s) this receipt is linked to.

        Combines the direct ``purchase_id`` link with the orders reached through
        each move's ``purchase_line_id`` (covers receipts that group several
        POs). Empty when the receipt is a standalone / non-PO incoming transfer.
        """
        self.ensure_one()
        return self.move_ids.purchase_line_id.order_id | self.purchase_id

    def button_validate(self):
        # Requirement 2: on goods receipt the received (done) quantity of each
        # line must not exceed its demand (ordered) quantity.
        for picking in self:
            if picking.picking_type_code != "incoming":
                continue
            offending = []
            for move in picking.move_ids:
                rounding = move.product_uom.rounding
                if float_compare(move.quantity, move.product_uom_qty, precision_rounding=rounding) > 0:
                    offending.append(
                        _(
                            "- %(product)s: received %(done)s > demand %(demand)s",
                            product=move.product_id.display_name,
                            done=move.quantity,
                            demand=move.product_uom_qty,
                        )
                    )
            if offending:
                raise UserError(
                    _(
                        "Received quantity cannot exceed the demand quantity on a goods receipt.\n\n%(lines)s",
                        lines="\n".join(offending),
                    )
                )
        return super().button_validate()
