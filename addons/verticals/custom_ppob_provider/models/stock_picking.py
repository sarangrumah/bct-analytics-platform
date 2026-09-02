# -*- coding: utf-8 -*-
"""stock.picking extension linking incoming/outgoing receipts to PPOB context.

When a bucket has ``inventory_product_id`` configured, two flows fire:

1. **Provider topup** -- after the DP 100% vendor bill is posted, the wizard
   creates an incoming picking validated immediately. The stock value mirrors
   the DPP credited to the bucket subledger.
2. **Mitra dispatch** -- when a transaction debits the bucket for a sale, an
   outgoing picking is created and validated to release the same stock units.

Note vs. ERA source: the ``x_custom_ppob_transaction_id`` back-reference is
declared by custom_ppob_sale (which owns ``custom.ppob.transaction``), not here,
so this provider module stays independently installable. ``_stock_picking_outgoing``
sets that link only when the column exists.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    x_custom_ppob_bucket_id = fields.Many2one(
        comodel_name="custom.ppob.provider.bucket",
        string="PPOB Bucket",
        copy=False,
        index=True,
    )
    x_custom_ppob_dp_invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Source DP Bill",
        copy=False,
    )


class PpobProviderBucket(models.Model):
    _inherit = "custom.ppob.provider.bucket"

    def _get_warehouse(self):
        self.ensure_one()
        if self.inventory_warehouse_id:
            return self.inventory_warehouse_id
        wh = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not wh:
            raise UserError(
                _("No stock.warehouse found for company %s. Create one before topping up an inventory-tracked bucket.")
                % self.company_id.name
            )
        return wh

    def _stock_picking_incoming(self, qty, *, origin=None, invoice=None, partner=None):
        """Create + validate an incoming picking for the bucket's inventory
        product. Used by the provider topup wizard."""
        self.ensure_one()
        if not self.inventory_product_id:
            return self.env["stock.picking"]
        if qty <= 0:
            raise UserError(_("Cannot create incoming picking: qty must be positive (got %s).") % qty)

        warehouse = self._get_warehouse()
        picking_type = warehouse.in_type_id
        if not picking_type:
            raise UserError(_("Warehouse %s has no incoming picking type configured.") % warehouse.display_name)

        src = picking_type.default_location_src_id.id or self.env.ref("stock.stock_location_suppliers").id
        dest = picking_type.default_location_dest_id.id
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "partner_id": (partner or self.provider_id.partner_id).id,
                "origin": origin or self.display_name,
                "location_id": src,
                "location_dest_id": dest,
                "company_id": self.company_id.id,
                "x_custom_ppob_bucket_id": self.id,
                "x_custom_ppob_dp_invoice_id": invoice.id if invoice else False,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "name": self.inventory_product_id.display_name,
                            "product_id": self.inventory_product_id.id,
                            "product_uom": self.inventory_product_id.uom_id.id,
                            "product_uom_qty": qty,
                            "location_id": src,
                            "location_dest_id": dest,
                        },
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        return picking

    def _stock_picking_outgoing(self, qty, *, origin=None, transaction=None, partner=None):
        """Create + validate an outgoing picking releasing inventory for a
        mitra transaction."""
        self.ensure_one()
        if not self.inventory_product_id:
            return self.env["stock.picking"]
        if qty <= 0:
            raise UserError(_("Cannot create outgoing picking: qty must be positive (got %s).") % qty)

        warehouse = self._get_warehouse()
        picking_type = warehouse.out_type_id
        if not picking_type:
            raise UserError(_("Warehouse %s has no outgoing picking type configured.") % warehouse.display_name)

        src = picking_type.default_location_src_id.id
        dest = picking_type.default_location_dest_id.id or self.env.ref("stock.stock_location_customers").id
        vals = {
            "picking_type_id": picking_type.id,
            "partner_id": partner.id if partner else False,
            "origin": origin or self.display_name,
            "location_id": src,
            "location_dest_id": dest,
            "company_id": self.company_id.id,
            "x_custom_ppob_bucket_id": self.id,
            "move_ids": [
                (
                    0,
                    0,
                    {
                        "name": self.inventory_product_id.display_name,
                        "product_id": self.inventory_product_id.id,
                        "product_uom": self.inventory_product_id.uom_id.id,
                        "product_uom_qty": qty,
                        "location_id": src,
                        "location_dest_id": dest,
                    },
                )
            ],
        }
        # Link to the originating transaction only when custom_ppob_sale has
        # declared the column (keeps provider independently installable).
        if transaction and "x_custom_ppob_transaction_id" in self.env["stock.picking"]._fields:
            vals["x_custom_ppob_transaction_id"] = transaction.id
        picking = self.env["stock.picking"].create(vals)
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()
        return picking
