# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import fields, models


class IrSequence(models.Model):
    _inherit = "ir.sequence"

    x_monthly_reset = fields.Boolean(
        string="Monthly Reset",
        help="When set together with 'Use subsequences per date_range', the "
        "running number resets every MONTH instead of every year. Stock Odoo "
        "only creates yearly date ranges; this flag makes the date range that "
        "is auto-created span a single calendar month.",
    )

    def _create_date_range_seq(self, date):
        """Create a one-month date range for monthly-reset sequences.

        Odoo's base implementation always creates a full-year range, which
        resets the counter yearly. For sequences flagged ``x_monthly_reset`` we
        create a range covering only the month of ``date`` so the counter
        starts again at 1 each month.
        """
        if self.x_monthly_reset:
            d = fields.Date.to_date(date)
            date_from = d.replace(day=1)
            date_to = date_from + relativedelta(months=1, days=-1)
            return (
                self.env["ir.sequence.date_range"]
                .sudo()
                .create(
                    {
                        "date_from": date_from,
                        "date_to": date_to,
                        "sequence_id": self.id,
                    }
                )
            )
        return super()._create_date_range_seq(date)
