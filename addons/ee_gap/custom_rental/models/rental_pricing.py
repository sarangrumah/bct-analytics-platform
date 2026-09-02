# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


UNIT_TO_HOURS = {
    "hour": 1.0,
    "day": 24.0,
    "week": 24.0 * 7,
    "month": 24.0 * 30,
}


class CustomRentalPricing(models.Model):
    """Per-period rental pricing tier attached to product.template.

    Multiple tiers can co-exist (e.g. 1 day, 1 week, 1 month) — when
    quoting a duration we pick the combination that yields the lowest
    total price.
    """

    _name = "custom.rental.pricing"
    _description = "Rental Pricing Tier"
    _order = "product_template_id, unit, duration"

    name = fields.Char(compute="_compute_name", store=True)
    product_template_id = fields.Many2one(
        "product.template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    duration = fields.Integer(required=True, default=1)
    unit = fields.Selection(
        [("hour", "Hour"), ("day", "Day"), ("week", "Week"), ("month", "Month")],
        required=True,
        default="day",
    )
    price = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda s: s.env.company.currency_id,
    )
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    active = fields.Boolean(default=True)

    @api.depends("duration", "unit", "price")
    def _compute_name(self):
        for rec in self:
            rec.name = "%d %s @ %s" % (rec.duration or 0, rec.unit or "", rec.price or 0.0)

    @api.constrains("duration", "price")
    def _check_positive(self):
        for rec in self:
            if rec.duration <= 0:
                raise ValidationError(_("Pricing duration must be > 0."))
            if rec.price < 0:
                raise ValidationError(_("Pricing price cannot be negative."))

    def _hours(self):
        self.ensure_one()
        return float(self.duration) * UNIT_TO_HOURS[self.unit]

    @api.model
    def _get_rental_price(self, product, start_dt, end_dt, currency=None):
        """Return total price applying the cheapest tier combination.

        :param product: product.product or product.template recordset
        :param start_dt: datetime
        :param end_dt: datetime
        :param currency: res.currency or None (defaults to company currency)
        :return: float total price
        """
        if not (start_dt and end_dt) or end_dt <= start_dt:
            return 0.0
        if product._name == "product.product":
            tmpl = product.product_tmpl_id
        else:
            tmpl = product
        tiers = tmpl.rental_pricing_ids.filtered("active").sorted(key=lambda p: p._hours())
        if not tiers:
            return 0.0
        duration_hours = (end_dt - start_dt).total_seconds() / 3600.0
        if duration_hours <= tiers[0]._hours():
            price = tiers[0].price
            ccy = currency or tiers[0].currency_id
            if currency and tiers[0].currency_id and currency != tiers[0].currency_id:
                price = tiers[0].currency_id._convert(
                    price,
                    currency,
                    self.env.company,
                    fields.Date.context_today(self),
                )
            return price
        remaining = duration_hours
        total = 0.0
        for tier in reversed(tiers):
            th = tier._hours()
            if th <= 0:
                continue
            count = int(remaining // th)
            if count > 0:
                total += count * tier.price
                remaining -= count * th
        if remaining > 0:
            total += tiers[0].price
        return total
