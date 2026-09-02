# -*- coding: utf-8 -*-
"""Gate ``purchase.order.button_confirm`` on approval state.

Clicking Confirm auto-submits the approval request (when a matrix matches)
and leaves the order in Waiting Approval; only orders that need no approval
or are already approved proceed. After the final tier approves, the engine
calls ``_approval_on_granted`` to auto-confirm.
"""

from __future__ import annotations

from odoo import models


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "approval.mixin"]

    def button_confirm(self):
        proceed = self.browse()
        for order in self:
            if order._approval_request_or_proceed():
                proceed |= order
        if proceed:
            return super(PurchaseOrder, proceed).button_confirm()
        return True

    def _approval_on_granted(self):
        return self.button_confirm()
