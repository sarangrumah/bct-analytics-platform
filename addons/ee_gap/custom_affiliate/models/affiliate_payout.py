# -*- coding: utf-8 -*-
from __future__ import annotations

from odoo import _, api, fields, models


class CustomAffiliatePayout(models.Model):
    _name = "custom.affiliate.payout"  # nosemgrep
    _description = "Affiliate Payout"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(required=True, copy=False, default=lambda self: self.env._("New"), readonly=True)
    affiliate_id = fields.Many2one("custom.affiliate", string="Affiliate", required=True, ondelete="restrict")
    date_from = fields.Date()
    date_to = fields.Date(default=fields.Date.context_today)
    conversion_ids = fields.One2many("custom.affiliate.conversion", "payout_id", string="Conversions")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    amount_total = fields.Monetary(compute="_compute_total", store=True, currency_field="currency_id")
    state = fields.Selection([("draft", "Draft"), ("paid", "Paid")], default="draft", required=True, tracking=True)
    method = fields.Selection(
        [("bank", "Bank Transfer"), ("ewallet", "E-Wallet"), ("manual", "Manual")],
        default="manual",
    )
    paid_date = fields.Date()

    @api.depends("conversion_ids.commission_amount")
    def _compute_total(self):
        for p in self:
            p.amount_total = sum(p.conversion_ids.mapped("commission_amount"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.affiliate.payout") or _("New")
        return super().create(vals_list)

    def action_collect_approved(self):
        """Attach all approved, unpaid conversions of the affiliate to this payout."""
        self.ensure_one()
        convs = self.env["custom.affiliate.conversion"].search(
            [
                ("affiliate_id", "=", self.affiliate_id.id),
                ("state", "=", "approved"),
                ("payout_id", "=", False),
            ]
        )
        convs.write({"payout_id": self.id})

    def action_mark_paid(self):
        for payout in self:
            if payout.state == "paid":
                continue
            payout.conversion_ids.write({"state": "paid"})
            payout.write({"state": "paid", "paid_date": fields.Date.context_today(payout)})
