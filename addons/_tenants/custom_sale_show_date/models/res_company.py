# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    x_custom_show_date_enabled = fields.Boolean(
        string="Enable Show Date (the tenant)",
        default=False,
        help="When enabled, quotations/sales orders for this company capture a "
        "required Show Date, and customer-invoice payment-term due dates are "
        "anchored to the Show Date instead of the invoice date. Intended for "
        "PT the tenant only; leave off for every other company.",
    )
