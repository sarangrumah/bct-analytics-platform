# -*- coding: utf-8 -*-
from odoo import _, fields, models


class CustomRentalLateFeeLine(models.Model):
    """One row per cron tick per overdue order — keeps an audit trail
    of why the cumulative late_fee_total grew on a given day."""

    _name = "custom.rental.late.fee.line"
    _description = "Rental Late Fee Daily Accrual"
    _order = "accrued_on desc, id desc"

    order_id = fields.Many2one(
        "rental.order",
        required=True,
        ondelete="cascade",
        index=True,
    )
    accrued_on = fields.Date(required=True, default=fields.Date.context_today)
    days_overdue = fields.Float(required=True)
    rate = fields.Float(string="Rate %", required=True)
    base_amount = fields.Monetary(currency_field="currency_id")
    fee_amount = fields.Monetary(currency_field="currency_id", required=True)
    currency_id = fields.Many2one("res.currency")
    note = fields.Char()

    _uniq_per_day = models.Constraint(
        "unique(order_id, accrued_on)",
        "Late fee already accrued for this order on this day.",
    )

    @staticmethod
    def _description_for_log(rec):
        return _("Late fee accrued: %s") % (rec.fee_amount,)
