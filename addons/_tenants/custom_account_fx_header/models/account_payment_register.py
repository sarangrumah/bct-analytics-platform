# -*- coding: utf-8 -*-
"""Foreign-currency context inside the "Register Payment" popup.

The bank journals of the tenant books are locked to the company currency
(IDR), so paying a CNY bill opens a wizard whose ``Amount`` is already the
*converted* IDR figure - Odoo's ``_convert_to_wizard_currency`` turns the CNY
residual into IDR at the payment date. Nothing about that number is raw.

The problem is that the popup never says so. All the user sees is an IDR
amount, with no trace of the CNY residual it settles nor of the rate that was
applied, so there is no way to tell a correct conversion from a missing one.
This adds that context, plus a loud warning for the one case where the amount
really would be raw: no ``res.currency.rate`` row for the document currency, in
which case ``_convert`` silently falls back to 1:1 and a CN¥ 20,000 bill would
propose "Rp 20.000".

Display only - no amount, rate or posting behaviour is changed.
"""

from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    x_fx_is_foreign = fields.Boolean(
        string="Foreign Currency Payment",
        compute="_compute_x_fx_values",
        help="The documents being paid are written in a currency other than the currency the payment is made in.",
    )
    x_fx_rate_payment_per_unit = fields.Float(
        string="Rate",
        compute="_compute_x_fx_values",
        digits=(16, 4),
        help="How many units of the payment currency one unit of the document "
        "currency is worth at the payment date. Display only.",
    )
    x_fx_amount_source = fields.Monetary(
        string="Equivalent in Document Currency",
        currency_field="source_currency_id",
        compute="_compute_x_fx_values",
        help="The amount entered above, converted back into the currency of the "
        "documents being paid, at the payment date.",
    )
    x_fx_rate_missing = fields.Boolean(
        string="Exchange Rate Missing",
        compute="_compute_x_fx_values",
        help="No exchange rate is defined for the document currency in this "
        "company. Odoo then converts 1:1, so the proposed amount is the raw "
        "foreign-currency figure.",
    )

    @api.depends(
        "source_currency_id",
        "currency_id",
        "company_id",
        "payment_date",
        "amount",
    )
    def _compute_x_fx_values(self):
        for wizard in self:
            source = wizard.source_currency_id
            payment = wizard.currency_id
            is_foreign = bool(source and payment and source != payment)
            wizard.x_fx_is_foreign = is_foreign
            if not is_foreign or not wizard.payment_date:
                wizard.x_fx_rate_payment_per_unit = 0.0
                wizard.x_fx_amount_source = 0.0
                wizard.x_fx_rate_missing = False
                continue

            company = wizard.company_id
            wizard.x_fx_rate_payment_per_unit = source._convert(1.0, payment, company, wizard.payment_date, round=False)
            wizard.x_fx_amount_source = payment._convert(wizard.amount, source, company, wizard.payment_date)
            wizard.x_fx_rate_missing = not wizard._x_fx_has_rate(source, company) or not wizard._x_fx_has_rate(
                payment, company
            )

    @api.model
    def _x_fx_has_rate(self, currency, company):
        """True when ``currency`` can be converted for ``company``.

        The company currency needs no rate row; any other currency does, and
        without one ``res.currency._convert`` silently uses 1.0.
        """
        if not currency or not company or currency == company.currency_id:
            return True
        return bool(
            self.env["res.currency.rate"]
            .sudo()
            .search_count(
                [
                    ("currency_id", "=", currency.id),
                    ("company_id", "in", (company.id, False)),
                ],
                limit=1,
            )
        )
