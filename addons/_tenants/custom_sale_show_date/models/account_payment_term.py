# -*- coding: utf-8 -*-
from odoo import models


class AccountPaymentTerm(models.Model):
    _inherit = "account.payment.term"

    def _compute_terms(self, date_ref, *args, **kwargs):
        """Anchor due dates to the tenant show date when requested.

        ``account.move._compute_needed_terms`` (for a flagged company's customer
        invoice with a show date) calls super() on the move with context
        ``tenant_show_date_ref`` set. Core then calls
        ``invoice.invoice_payment_term_id._compute_terms(date_ref=invoice_date, ...)``;
        the related-field access preserves the env/context, so we read the key
        here and substitute it for ``date_ref``. Every ``date_maturity`` and the
        ``discount_date`` are then computed relative to the show date instead of
        the invoice date, with no duplication of core term logic.

        ``date_ref`` is the first positional arg of
        ``account.payment.term._compute_terms`` in Odoo 19.0. The key is only
        ever set by the tenant move override, so this is a pure pass-through for
        every other company, module, and tenant.
        """
        show_ref = self.env.context.get("tenant_show_date_ref")
        if show_ref:
            date_ref = show_ref
        return super()._compute_terms(date_ref, *args, **kwargs)
