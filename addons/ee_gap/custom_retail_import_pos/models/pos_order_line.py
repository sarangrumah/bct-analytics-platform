# -*- coding: utf-8 -*-
"""Make imported POS lines book the source workbook's own tax and contra accounts.

POS session close does *not* use the ``price_subtotal`` stored on a line: it rebuilds
base lines from ``price_unit`` / ``qty`` / ``tax_ids`` and re-runs the tax engine
(``pos.session._accumulate_amounts``). Forcing ``price_subtotal`` at import time is
therefore invisible in the GL. This module hooks the one place that *is* honoured —
``account.tax``'s ``manual_tax_amounts`` / ``manual_total_excluded`` base-line keys.
"""

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    ri_src_net = fields.Monetary(
        string="Source Net Amount",
        readonly=True,
        help="NET SOLD AMOUNT as it appears in the source retail workbook (X24DN/X48). "
        "Signed as in the file (negative for returns).",
    )
    ri_src_tax = fields.Monetary(
        string="Source Tax Amount",
        readonly=True,
        help="TAX AMOUNT as it appears in the source retail workbook. Booked verbatim, "
        "since the file truncates net per line while Odoo rounds tax per order.",
    )
    ri_src_discount = fields.Monetary(
        string="Source Discount Amount",
        readonly=True,
        help="NET DISCOUNT AMOUNT as it appears in the source workbook. Consumed by the "
        "discount reclass; never recomputed.",
    )
    ri_is_return = fields.Boolean(
        string="Imported Customer Return",
        readonly=True,
        help="Set on X48 refund lines and on X24DN lines with a negative NET SOLD "
        "QUANTITY (in-store exchange), so the amount is booked to "
        "Sales Return-<category> instead of reversing Gross Sales-<category>.",
    )

    # --- Source columns kept for traceability (no GL effect) ------------------
    # X24DN carries who sold the line and, in up to four slots, why it was
    # discounted. None of it reached Odoo before, so a discounted POS line could
    # not be traced back to its promo without reopening the workbook.
    ri_staff_id = fields.Char(
        string="Source Staff ID",
        readonly=True,
        help="STAFF ID as it appears in the source retail workbook (X24DN).",
    )
    ri_staff_name = fields.Char(
        string="Source Staff Name",
        readonly=True,
        help="STAFF NAME as it appears in the source retail workbook (X24DN).",
    )
    ri_discount_type = fields.Char(
        string="Source Discount Type",
        readonly=True,
        help="DISCOUNT TYPE of every populated discount slot, ' | '-joined (e.g. TRANSACTION_DISCOUNT).",
    )
    ri_discount_code = fields.Char(
        string="Source Discount Code",
        readonly=True,
        help="DISCOUNT CODE of every populated discount slot, ' | '-joined. Used to "
        "label the discount reclass journal lines.",
    )
    ri_discount_description = fields.Char(
        string="Source Discount Description",
        readonly=True,
        help="DISCOUNT DESCRIPTION of every populated discount slot, ' | '-joined (e.g. CRM-VIP DISCOUNT).",
    )
    ri_line_comment = fields.Char(
        string="Source Line Comment",
        readonly=True,
        help="LINE ITEM - COMMENTS as it appears in the source retail workbook.",
    )

    def _ri_return_account(self):
        """Sales Return-<category> account for this line, or an empty recordset."""
        self.ensure_one()
        company = self.order_id.company_id
        return self.env["retail.import.executor"]._ri_category_account(company, self.product_id, "return")

    def _prepare_base_line_for_taxes_computation(self):
        base_line = super()._prepare_base_line_for_taxes_computation()
        if not (self.ri_src_net or self.ri_src_tax):
            return base_line

        if self.ri_is_return:
            account = self._ri_return_account()
            if account:
                base_line["account_id"] = account
            else:
                _logger.warning(
                    "retail import: no Sales Return account for product %s (category %s); "
                    "refund stays on the income account",
                    self.product_id.display_name,
                    self.product_id.categ_id.display_name,
                )

        taxes = self.tax_ids_after_fiscal_position
        if len(taxes) != 1:
            # The manual amounts below are keyed per tax and cannot be apportioned
            # across a multi-tax line; leave Odoo's computation alone.
            return base_line

        # The manual amounts replace ``total_excluded``, which the tax engine derives
        # from ``price_unit * quantity`` — so they must carry that product's sign, NOT
        # the sign of the source column and NOT an unconditional ``abs()``.
        #
        # The order-level ``sign`` (credit/debit direction) is applied on top downstream,
        # so for a plain sale line (quantity > 0) and for an X48 refund line (POS flips
        # ``quantity`` positive on a refund order) the magnitude is positive. A negative
        # line *inside* a sale order — X24DN records in-transaction exchanges as a
        # ``qty=-1`` line paired with a ``qty=+1`` line — keeps ``quantity < 0`` and must
        # stay negative, otherwise the reversal is booked as a second sale and the
        # unmatched tender lands in Cash Difference Loss.
        signed = base_line["quantity"] * base_line["price_unit"]
        direction = -1.0 if signed < 0 else 1.0
        net = direction * abs(self.ri_src_net)
        tax_amount = direction * abs(self.ri_src_tax)
        base_line["manual_total_excluded_currency"] = net
        base_line["manual_total_excluded"] = net
        base_line["manual_tax_amounts"] = {
            str(taxes.id): {
                "base_amount_currency": net,
                "base_amount": net,
                "tax_amount_currency": tax_amount,
                "tax_amount": tax_amount,
            }
        }
        return base_line
