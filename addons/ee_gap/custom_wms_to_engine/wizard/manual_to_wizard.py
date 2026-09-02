# -*- coding: utf-8 -*-
"""Manual transfer-order creation wizard."""

from __future__ import annotations

from odoo import _, fields, models
from odoo.exceptions import UserError


class ManualToWizard(models.TransientModel):
    _name = "custom.transfer.order.manual.wizard"
    _description = "Manual Transfer Order Wizard"

    product_id = fields.Many2one("product.product", required=True)
    source_location_id = fields.Many2one("stock.location", required=True)
    target_location_id = fields.Many2one("stock.location", required=True)
    qty = fields.Float(required=True, default=0.0)
    deadline_at = fields.Datetime()
    priority = fields.Integer(default=10)

    def action_create(self):
        self.ensure_one()
        if self.qty <= 0:
            raise UserError(_("Qty must be greater than zero."))
        if self.source_location_id == self.target_location_id:
            raise UserError(_("Source and target must differ."))
        engine = self.env["custom.to.engine"]
        TO = self.env["custom.transfer.order"]
        to = TO.create(
            {
                "source_location_id": self.source_location_id.id,
                "target_location_id": self.target_location_id.id,
                "product_id": self.product_id.id,
                "planned_qty": self.qty,
                "state": "proposed",
            }
        )
        engine.materialize(
            {
                "source_location_id": self.source_location_id.id,
                "target_location_id": self.target_location_id.id,
                "product_id": self.product_id.id,
                "planned_qty": self.qty,
                "name": to.name,
                "company_id": self.env.company.id,
            },
            transfer_order=to,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Transfer Order"),
            "res_model": "custom.transfer.order",
            "res_id": to.id,
            "view_mode": "form",
        }
