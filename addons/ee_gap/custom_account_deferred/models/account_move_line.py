# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    deferred_start_date = fields.Date(
        string="Deferred Start",
        copy=False,
        help="Spread this line's P&L amount from this date (inclusive).",
    )
    deferred_end_date = fields.Date(
        string="Deferred End",
        copy=False,
        help="…through this date (inclusive). One recognition entry per month.",
    )

    @api.constrains("deferred_start_date", "deferred_end_date")
    def _check_deferred_dates(self):
        for line in self:
            if bool(line.deferred_start_date) != bool(line.deferred_end_date):
                raise ValidationError(_("Deferred dates: set both start and end, or neither."))
            if line.deferred_start_date and line.deferred_end_date < line.deferred_start_date:
                raise ValidationError(_("Deferred end date must be on/after the start."))

    def _is_deferrable(self):
        """P&L lines of an invoice/bill/refund with a date range set."""
        self.ensure_one()
        return (
            self.deferred_start_date
            and self.deferred_end_date
            and self.display_type == "product"
            and self.account_id.internal_group in ("income", "expense")
        )
