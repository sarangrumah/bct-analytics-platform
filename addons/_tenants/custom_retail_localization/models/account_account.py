# -*- coding: utf-8 -*-
"""Allow selected non-receivable/payable accounts as a payment Destination Account.

Odoo restricts the payment "Destination Account" to receivable/payable accounts.
the apparel brand needs to book certain payments (down payments, advances, security
deposits) straight against an asset account. Rather than reclassify those
accounts (which would corrupt their nature) or widen the domain to every asset
account, we let Accounting flag individual accounts as usable payment
destinations. The payment form/register then offers the native receivable/
payable set *plus* any flagged account.
"""

from __future__ import annotations

from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = "account.account"

    l10n_allow_payment_destination = fields.Boolean(
        string="Bisa jadi Destination Account (Payment)",
        help="Centang agar akun ini dapat dipilih sebagai Destination Account "
        "pada Payment, meskipun bukan akun piutang/hutang. Untuk uang muka "
        "(down payment), advance, dan security deposit.",
    )
