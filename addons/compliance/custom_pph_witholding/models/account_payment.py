# -*- coding: utf-8 -*-
"""Lazy hook on account.payment.

When a vendor payment is posted, we log a witholding application against
the linked vendor bill so the bupot module can pick it up later.
"""

from __future__ import annotations

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    def action_post(self):
        result = super().action_post()
        try:
            self._custom_pph_log_witholding()
        except Exception as e:  # pragma: no cover - never block posting
            _logger.warning("PPh witholding log on payment failed: %s", e)
        return result

    def _custom_pph_log_witholding(self):
        Engine = self.env["custom.witholding.engine"]
        for payment in self:
            if payment.payment_type != "outbound":
                continue
            bills = getattr(payment, "reconciled_bill_ids", payment.browse([]))
            for bill in bills:
                # Detect any negative-amount tax on the bill as a proxy
                # for "withholding tax was applied here".
                has_wh = any(line.tax_line_id and line.tax_line_id.amount < 0 for line in bill.line_ids)
                if not has_wh:
                    continue
                Engine.compute_and_log(
                    partner=bill.partner_id,
                    amount=bill.amount_untaxed,
                    pph_type="23",
                    date=payment.date,
                    source_doc=payment,
                    state="applied",
                )
