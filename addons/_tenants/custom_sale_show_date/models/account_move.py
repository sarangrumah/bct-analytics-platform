# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    x_custom_show_date = fields.Date(
        string="Show Date",
        copy=False,
        help="Show date carried from the sales order. For companies with Show "
        "Date enabled, the customer-invoice payment-term due dates are anchored "
        "to this date instead of the invoice date.",
    )

    # Re-declares the EXACT core @api.depends list (verified against
    # account/models/account_move.py@19.0) PLUS the two new triggers. Omitting
    # any of the original five silently breaks recompute on that field.
    @api.depends(
        "invoice_payment_term_id",
        "invoice_date",
        "currency_id",
        "amount_total_in_currency_signed",
        "invoice_date_due",
        "x_custom_show_date",
        "company_id.x_custom_show_date_enabled",
    )
    def _compute_needed_terms(self):
        # Anchored = flagged-company customer invoices that carry a show date.
        anchored = self.filtered(
            lambda m: m.company_id.x_custom_show_date_enabled and m.move_type == "out_invoice" and m.x_custom_show_date
        )
        rest = self - anchored

        if rest:
            super(AccountMove, rest)._compute_needed_terms()

        # Per-record: the anchor is move-specific, so each must call super with
        # its own context. account.payment.term._compute_terms consumes the key.
        for move in anchored:
            super(
                AccountMove,
                move.with_context(tenant_show_date_ref=move.x_custom_show_date),
            )._compute_needed_terms()
