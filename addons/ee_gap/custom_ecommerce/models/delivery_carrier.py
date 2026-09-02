# -*- coding: utf-8 -*-
"""Indonesia-specific extensions of ``delivery.carrier``.

Adds:

- :attr:`DeliveryCarrier.x_id_courier_id` link to our Indonesian courier
  registry (``custom.ecommerce.courier``).
- :attr:`x_id_service_type` per-carrier service code (REG, YES, OKE, …).
- :attr:`x_id_cod_supported` flag + :attr:`cod_max_amount` ceiling for
  Cash-on-Delivery validation on the sale.order.
- :meth:`_get_id_shipping_rate` — mock pricing entry point that downstream
  RajaOngkir/Komerce adapters can override. Returns a dict shaped like a
  real adapter response (cost, etd, service, courier_code) so the calling
  code never has to branch on “mock vs live”.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Coarse intra-city / inter-city base rates per kg (IDR). Hand-tuned to be
# in the right order of magnitude vs. real JNE REG / J&T EZ pricing — good
# enough for dev seeds, demo data and unit tests.
_BASE_RATE_PER_KG = {
    "jne": 12000,
    "jnt": 11000,
    "sicepat": 10500,
    "anteraja": 10000,
    "posindo": 9000,
    "grab": 18000,
    "gojek": 18000,
    "custom": 12000,
}

# Multiplier applied on top of base rate depending on the service code.
# REG is the baseline; YES/express adds a premium; OKE/economy is cheaper.
_SERVICE_MULTIPLIER = {
    "REG": 1.0,
    "YES": 1.6,
    "OKE": 0.85,
    "ECO": 0.8,
    "EXP": 1.8,
    "SAMEDAY": 2.2,
    "INSTANT": 2.5,
}


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    x_id_courier_id = fields.Many2one(
        "custom.ecommerce.courier",
        string="Indonesian Courier",
    )
    x_id_service_type = fields.Char(string="Service Type Code")
    x_id_cod_supported = fields.Boolean(
        string="COD Supported",
        default=False,
    )
    cod_max_amount = fields.Monetary(
        string="COD Max Amount",
        currency_field="currency_id",
        help=(
            "Maximum order total accepted when paying Cash-on-Delivery via "
            "this carrier. Orders exceeding this ceiling are rejected at "
            "confirmation. 0.0 disables the ceiling check."
        ),
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    # -------- Shipping rate (mock; RajaOngkir/Komerce-ready) --------

    def _get_id_shipping_rate(self, order) -> dict:
        """Return shipping rate for ``order`` via the Indonesian courier.

        Mock implementation — origin/destination derived from
        ``warehouse_id.partner_id.zip`` and ``partner_shipping_id.zip``,
        weight derived from sum of order lines’ product weight × qty,
        and price computed from :data:`_BASE_RATE_PER_KG` ×
        :data:`_SERVICE_MULTIPLIER`. A later RajaOngkir / Komerce adapter
        can override this method without touching call sites.
        """
        self.ensure_one()
        if not self.x_id_courier_id:
            raise UserError(_("Carrier %s has no Indonesian courier linked.") % self.name)

        origin_zip = ""
        if order and order.warehouse_id and order.warehouse_id.partner_id:
            origin_zip = order.warehouse_id.partner_id.zip or ""
        dest_zip = ""
        if order and order.partner_shipping_id:
            dest_zip = order.partner_shipping_id.zip or ""

        # Weight in kg — fall back to 1 kg if products have no weight set.
        weight = 0.0
        for line in order.order_line if order else []:
            if line.product_id and not line.is_delivery:
                weight += (line.product_id.weight or 0.0) * (line.product_uom_qty or 0.0)
        weight = max(weight, 1.0)

        code = (self.x_id_courier_id.code or "custom").lower()
        base = _BASE_RATE_PER_KG.get(code, _BASE_RATE_PER_KG["custom"])
        service = (self.x_id_service_type or "REG").upper()
        multiplier = _SERVICE_MULTIPLIER.get(service, 1.0)

        # Distance proxy: same first 2 zip digits = intra-province → 1.0x,
        # otherwise inter-province → 1.4x. Cheap heuristic, sufficient
        # for the mock.
        distance_factor = 1.0
        if origin_zip and dest_zip and origin_zip[:2] != dest_zip[:2]:
            distance_factor = 1.4

        cost = round(base * weight * multiplier * distance_factor, 0)

        return {
            "ok": True,
            "courier_code": code,
            "service": service,
            "weight_kg": weight,
            "origin_zip": origin_zip,
            "destination_zip": dest_zip,
            "cost": cost,
            "currency": "IDR",
            "etd_days": "2-3" if multiplier <= 1.0 else "1-2",
            "raw": {"mock": True},
        }

    # -------- delivery.carrier API hook --------

    def id_rate_shipment(self, order):
        """Adapter for the standard ``delivery.carrier.rate_shipment`` shape.

        Returns the dict that Odoo's delivery framework expects:
        ``{'success': bool, 'price': float, 'error_message': str, ...}``.
        Wraps :meth:`_get_id_shipping_rate` so the ID flow can be plugged
        into ``delivery_type='fixed'`` rows seamlessly later.
        """
        self.ensure_one()
        try:
            rate = self._get_id_shipping_rate(order)
        except UserError as e:
            return {
                "success": False,
                "price": 0.0,
                "error_message": str(e),
                "warning_message": False,
            }
        return {
            "success": True,
            "price": rate["cost"],
            "error_message": False,
            "warning_message": False,
            "id_rate": rate,
        }

    @api.constrains("cod_max_amount", "x_id_cod_supported")
    def _check_cod_max(self):
        for rec in self:
            if rec.cod_max_amount and rec.cod_max_amount < 0:
                raise UserError(_("COD Max Amount cannot be negative."))
