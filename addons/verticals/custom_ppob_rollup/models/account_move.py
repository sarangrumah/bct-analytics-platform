# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    x_custom_ppob_is_summary = fields.Boolean(
        string="PPOB Summary Invoice",
        default=False,
        index=True,
        help="Marks this invoice as the per-mitra-day PPOB rollup faktur. GL "
        "revenue is already booked on the per-transaction moves; this "
        "document lives on a report-excluded journal for audit + e-Faktur.",
    )
