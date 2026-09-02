# -*- coding: utf-8 -*-
"""Trade / Non-Trade account mapping.

Feature #9 splits every purchase into a **trade** stream (merchandise for
resale) and a **non-trade** stream (operational / opex). The two streams post
to different accounts:

* the **payable** account on the vendor bill (Trade Payables vs Non-Trade
  payable), and
* the **GR/IR clearing** (stock-variation) account used by the platform-way GR
  valuation journal for storable lines.

Because account *codes* are company-dependent in Odoo 19 and the numeric ids
differ per database, the mapping is a data table keyed by ``(company,
purchase_type)`` rather than hard xml-ids. Rows are seeded idempotently by code
lookup (see ``custom_retail_localization`` post-init hook / setup script) and can
be adjusted from Accounting ▸ Configuration ▸ Trade/Non-Trade Accounts.
"""

from odoo import api, fields, models


class RetailPurchaseAccountMap(models.Model):
    _name = "retail.purchase.account.map"
    _description = "the apparel brand Trade/Non-Trade Purchase Account Mapping"
    _order = "company_id, purchase_type"

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
    )
    purchase_type = fields.Selection(
        [("trade", "Trade"), ("non_trade", "Non-Trade")],
        string="Purchase Type",
        required=True,
    )
    payable_account_id = fields.Many2one(
        "account.account",
        string="Payable Account",
        domain="[('account_type', '=', 'liability_payable')]",
        help="Payable (AP) account used on the vendor bill for this stream.",
    )
    grir_account_id = fields.Many2one(
        "account.account",
        string="GR/IR Clearing Account",
        help="Stock-variation / GR-IR clearing account used by the goods-receipt "
        "valuation journal for storable lines of this stream. Leave empty for "
        "trade to keep the product category's own per-category GR/IR account.",
    )
    expense_account_id = fields.Many2one(
        "account.account",
        string="Default Expense Account",
        help="Optional default expense account for non-trade, non-storable "
        "purchases (informational; product/category accounts still win).",
    )

    _company_type_uniq = models.Constraint(
        "unique(company_id, purchase_type)",
        "Only one account mapping per company and purchase type is allowed.",
    )

    def name_get(self):
        labels = dict(self._fields["purchase_type"]._description_selection(self.env))
        return [
            (rec.id, "%s / %s" % (rec.company_id.name, labels.get(rec.purchase_type, rec.purchase_type)))
            for rec in self
        ]

    @api.model
    def _get_map(self, company, purchase_type):
        """Return the mapping row for ``company``/``purchase_type`` or empty rs."""
        if not company or not purchase_type:
            return self.browse()
        return self.sudo().search(
            [
                ("company_id", "=", company.id),
                ("purchase_type", "=", purchase_type),
            ],
            limit=1,
        )
