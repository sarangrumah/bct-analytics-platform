# -*- coding: utf-8 -*-
"""Operating-Unit wiring on the warehouse (store).

Each the apparel brand store is a ``stock.warehouse``. Feature #9 turns the store into a
posted **Operating Unit** dimension: every store owns

* an ``account.analytic.account`` (in the "Operating Unit" analytic plan) that is
  stamped on PO / bill / GR-journal lines so journals and P&L can be sliced per
  store, and
* a dedicated **purchase journal** so vendor bills are separated per store.

The links are populated idempotently by the module seeding (post-init hook /
``40_setup_trade_ou.py``); the fields here just hold them.
"""

from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    l10n_ou_analytic_id = fields.Many2one(
        "account.analytic.account",
        string="Operating Unit (Analytic)",
        help="Analytic account representing this store as an Operating Unit. "
        "Stamped on purchase / bill / goods-receipt journal lines.",
    )
    l10n_purchase_journal_id = fields.Many2one(
        "account.journal",
        string="Store Purchase Journal",
        domain="[('type', '=', 'purchase')]",
        help="Dedicated purchase journal for vendor bills of this store.",
    )
