# -*- coding: utf-8 -*-
"""Extra manual payment methods for the apparel brand (feedback Finance-AP #7).

The bank-out / bank-in flows need CHECK, GIRO and BANK TRANSFER as selectable
payment methods. CHECK already ships as ``check_printing``; GIRO and BANK
TRANSFER are registered here as manual-style methods (``mode='multi'`` so they
can be added to any bank journal). The ``account.payment.method`` records
themselves are created in ``data/payment_methods.xml``; a method's ``code`` must
be known to this dict at create time, hence the override.
"""

from odoo import models

RETAIL_PAYMENT_METHODS = ("giro", "bank_transfer")


class AccountPaymentMethod(models.Model):
    _inherit = "account.payment.method"

    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        for code in RETAIL_PAYMENT_METHODS:
            res[code] = {"mode": "multi", "type": ("bank",)}
        return res
