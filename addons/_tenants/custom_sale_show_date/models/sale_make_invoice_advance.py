# -*- coding: utf-8 -*-
from odoo import models


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    def _prepare_down_payment_invoice_line_values(self, order, so_line, account):
        """Label the DP invoice line with the products instead of "Down payment".

        Rewriting the stored name (rather than patching the QWeb template and the
        coretax exporter separately) fixes both printouts at once, because both
        read this field. custom_report_templates and custom_coretax_export are
        shared addons — leaving them untouched keeps every other tenant safe.
        """
        values = super()._prepare_down_payment_invoice_line_values(order, so_line, account)
        if self.advance_payment_method == "percentage":
            # %g so 50.0 prints as "50", matching the client's own paperwork
            # (core would write "50.00").
            marker = "(Uang Muka %g%%)" % self.amount
        else:
            marker = "(Uang Muka)"
        description = order._custom_down_payment_description(marker)
        if description:
            values["name"] = description
        return values
