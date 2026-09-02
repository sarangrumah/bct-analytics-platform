# -*- coding: utf-8 -*-
"""Head-Office Operating Unit pointer (feature #9 extension).

The Operating-Unit dimension (feature #9) gives every store (``stock.warehouse``)
its own analytic account in the "Operating Unit" plan. Head Office, however, is
the *company itself* rather than a warehouse, so it has no warehouse-owned
analytic. This field holds the company's Head-Office OU analytic, seeded once by
``setup.seed_trade_ou`` and offered alongside the store OUs on manual bills and
payments so P&L can be split HO vs Store.
"""

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_ho_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Head Office Operating Unit",
        copy=False,
        help="Analytic account (in the 'Operating Unit' plan) representing this "
        "company's Head Office. Seeded automatically; picked on bills/payments "
        "that belong to Head Office rather than a store.",
    )
