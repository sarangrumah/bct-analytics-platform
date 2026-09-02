# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SubscriptionPlan(models.Model):
    _name = "subscription.plan"
    _description = "Subscription Plan"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, copy=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    recurring_interval = fields.Selection(
        [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("yearly", "Yearly")],
        default="monthly",
        required=True,
    )
    recurring_count = fields.Integer(string="Every (N intervals)", default=1, required=True)
    price = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda s: s.env.company.currency_id.id,
        required=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Billing Product",
        required=True,
        domain="[('sale_ok','=',True)]",
    )
    trial_days = fields.Integer(default=0)

    _code_uniq = models.Constraint(
        "unique(code)",
        "Plan code must be unique.",
    )

    @api.constrains("recurring_count")
    def _check_recurring_count(self):
        for rec in self:
            if rec.recurring_count < 1:
                raise ValidationError("Recurring count must be >= 1.")
