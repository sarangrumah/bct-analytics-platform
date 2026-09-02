# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    x_custom_report_excluded = fields.Boolean(
        string="Exclude from Financial Reports",
        default=False,
        help="When set, move lines on this journal are omitted from every "
        "custom.report.engine report (Trial Balance, P&L, Balance Sheet, "
        "General Ledger, etc.). Use for document-only / memo journals "
        "whose GL effect must not reach the financial statements -- e.g. "
        "the PPOB daily summary (e-Faktur) journal whose revenue is "
        "already booked per transaction.",
    )
