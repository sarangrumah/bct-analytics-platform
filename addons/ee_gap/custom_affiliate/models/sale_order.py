# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    custom_affiliate_code = fields.Char(
        string="Affiliate Code",
        copy=False,
        help="Affiliate identifier captured from the storefront cookie at checkout.",
    )
    custom_affiliate_id = fields.Many2one("custom.affiliate", string="Affiliate", copy=False, readonly=True)

    def action_confirm(self):
        res = super().action_confirm()
        # Attribute on confirmation so cancelled drafts never accrue commission.
        for order in self:
            try:
                order._affiliate_create_conversion()
            except Exception:  # never let attribution break order confirmation
                _logger.exception("Affiliate attribution failed for %s", order.name)
        return res

    def _affiliate_create_conversion(self):
        self.ensure_one()
        if not self.custom_affiliate_code:
            return
        Affiliate = self.env["custom.affiliate"]
        affiliate = Affiliate._resolve_active(self.custom_affiliate_code)
        if not affiliate:
            return  # unknown/inactive code → ignored silently
        # Anti-fraud: block self-referral.
        if affiliate.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id:
            return
        Conversion = self.env["custom.affiliate.conversion"].sudo()
        if Conversion.search_count([("sale_order_id", "=", self.id)]):
            return  # already attributed
        base = self.amount_untaxed  # commission on goods value, excl tax/shipping
        rate = affiliate.commission_rate
        self.custom_affiliate_id = affiliate.id
        Conversion.create(
            {
                "affiliate_id": affiliate.id,
                "sale_order_id": self.id,
                "order_value": base,
                "commission_rate": rate,
                "commission_amount": base * rate / 100.0,
                "state": "pending",
            }
        )
