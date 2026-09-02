# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PpobCommissionRule(models.Model):
    _name = "custom.ppob.commission.rule"
    _description = "PPOB Commission Rule"
    _order = "priority, id"

    name = fields.Char(required=True)
    scope = fields.Selection(
        selection=[
            ("provider_to_us", "Provider -> Us (income)"),
            ("us_to_mitra", "Us -> Mitra (rebate expense)"),
        ],
        required=True,
    )
    priority = fields.Integer(default=100, help="Lower = evaluated first.")
    active = fields.Boolean(default=True)
    class_id = fields.Many2one("custom.ppob.product.class", help="Blank matches any class.")
    product_id = fields.Many2one("custom.ppob.product", help="Blank matches any product in the class.")
    partner_id = fields.Many2one(
        "res.partner",
        help="Provider (scope=provider_to_us) or mitra (scope=us_to_mitra). Blank matches any partner.",
    )
    calc_type = fields.Selection(
        selection=[
            ("percent_margin", "% of margin"),
            ("percent_sell", "% of sell price"),
            ("fixed", "Fixed amount per transaction"),
        ],
        default="percent_margin",
        required=True,
    )
    rate = fields.Float(required=True, help="Percent (0..100) or fixed amount per txn.")
    valid_from = fields.Date()
    valid_to = fields.Date()

    @api.constrains("rate", "calc_type")
    def _check_rate(self):
        for rule in self:
            if rule.calc_type in ("percent_margin", "percent_sell"):
                if rule.rate < 0 or rule.rate > 100:
                    raise ValidationError(_("Percent rate must be between 0 and 100."))
            elif rule.rate < 0:
                raise ValidationError(_("Fixed rate must be non-negative."))

    def _matches(self, transaction):
        self.ensure_one()
        if not self.active:
            return False
        today = fields.Date.context_today(self)
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        if self.class_id and self.class_id.id != transaction.class_id.id:
            return False
        if self.product_id and self.product_id.id != transaction.product_id.id:
            return False
        if self.partner_id:
            if self.scope == "provider_to_us" and self.partner_id != transaction.provider_id.partner_id:
                return False
            if self.scope == "us_to_mitra" and self.partner_id != transaction.mitra_id:
                return False
        return True

    def _compute_amount(self, transaction):
        self.ensure_one()
        if self.calc_type == "percent_margin":
            return max(0.0, (transaction.margin or 0.0) * (self.rate / 100.0))
        if self.calc_type == "percent_sell":
            return max(0.0, (transaction.sell_price or 0.0) * (self.rate / 100.0))
        return self.rate
