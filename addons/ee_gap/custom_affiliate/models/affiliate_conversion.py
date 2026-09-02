# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import api, fields, models


class CustomAffiliateConversion(models.Model):
    _name = "custom.affiliate.conversion"  # nosemgrep
    _description = "Affiliate Conversion"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    affiliate_id = fields.Many2one(
        "custom.affiliate", string="Affiliate", required=True, ondelete="cascade", index=True
    )
    sale_order_id = fields.Many2one("sale.order", string="Order", required=True, ondelete="cascade")
    order_reference = fields.Char(related="sale_order_id.name", store=True, readonly=True)
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id", store=True, readonly=True)
    order_value = fields.Monetary(currency_field="currency_id")
    commission_rate = fields.Float(string="Rate (%)")
    commission_amount = fields.Monetary(currency_field="currency_id")
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("reversed", "Reversed"),
            ("paid", "Paid"),
        ],
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )
    approve_date = fields.Datetime()
    payout_id = fields.Many2one("custom.affiliate.payout", ondelete="set null")

    _order_uniq = models.Constraint(
        "unique(sale_order_id)",
        "An order can be attributed only once.",
    )

    def action_approve(self):
        self.filtered(lambda c: c.state == "pending").write(
            {"state": "approved", "approve_date": fields.Datetime.now()}
        )

    def action_reverse(self):
        self.filtered(lambda c: c.state in ("pending", "approved")).write({"state": "reversed"})

    @api.model
    def _cron_affiliate_maintenance(self):
        """Reverse cancelled-order conversions, then approve aged pending ones."""
        ICP = self.env["ir.config_parameter"].sudo()
        window = int(ICP.get_param("custom_affiliate.reversal_window_days", "14"))
        # 1) reverse anything whose order got cancelled
        to_reverse = self.search(
            [
                ("state", "in", ("pending", "approved")),
                ("sale_order_id.state", "=", "cancel"),
            ]
        )
        to_reverse.action_reverse()
        # 2) approve pending conversions older than the reversal window
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=window)
        to_approve = self.search(
            [
                ("state", "=", "pending"),
                ("create_date", "<=", cutoff),
                ("sale_order_id.state", "!=", "cancel"),
            ]
        )
        to_approve.action_approve()
        return True
