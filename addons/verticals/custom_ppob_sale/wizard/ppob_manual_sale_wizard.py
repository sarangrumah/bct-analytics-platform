# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError


class PpobManualSaleWizard(models.TransientModel):
    _name = "custom.ppob.manual.sale.wizard"
    _description = "Manual PPOB Sale (ops-only; bypasses mitra API)"

    mitra_id = fields.Many2one(
        "res.partner",
        required=True,
        domain=[("x_custom_ppob_is_mitra", "=", True)],
    )
    product_id = fields.Many2one("custom.ppob.product", required=True)
    msisdn = fields.Char(required=True)
    provider_id = fields.Many2one("custom.ppob.provider", help="Leave empty to auto-route.")
    sell_price = fields.Monetary(required=True, currency_field="currency_id")
    cost_price = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.onchange("mitra_id", "product_id")
    def _onchange_resolve_price(self):
        if self.mitra_id and self.product_id:
            tier = self.mitra_id.x_custom_ppob_mitra_tier_id
            if tier:
                try:
                    self.sell_price = tier._get_sell_price(self.mitra_id, self.product_id)
                except UserError:
                    pass
        if self.product_id:
            self.cost_price = self.product_id.cost_price_default or self.cost_price

    def action_create_and_dispatch(self):
        self.ensure_one()
        txn = self.env["custom.ppob.transaction"].create(
            {
                "mitra_id": self.mitra_id.id,
                "product_id": self.product_id.id,
                "msisdn": self.msisdn,
                "provider_id": self.provider_id.id if self.provider_id else False,
                "sell_price": self.sell_price,
                "cost_price": self.cost_price or self.product_id.cost_price_default,
                "idempotency_key": "MANUAL/" + secrets.token_hex(6).upper(),
            }
        )
        txn.action_dispatch()
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.ppob.transaction",
            "res_id": txn.id,
            "view_mode": "form",
        }
