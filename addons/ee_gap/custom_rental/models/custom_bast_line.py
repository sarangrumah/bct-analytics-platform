# -*- coding: utf-8 -*-
from odoo import fields, models


class CustomBastLine(models.Model):
    _inherit = "custom.bast.line"

    is_loan = fields.Boolean(
        help="Loan/cadangan unit shipped with the rental — must be returned at end of contract.",
    )
