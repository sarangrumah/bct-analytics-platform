# -*- coding: utf-8 -*-
"""Gate ``sale.order.action_confirm`` on approval state.

Clicking Confirm auto-submits the approval request (when a matrix matches)
and leaves the order in Waiting Approval; only orders that need no approval
or are already approved proceed. After the final tier approves, the engine
calls ``_approval_on_granted`` to auto-confirm.
"""

from __future__ import annotations

from odoo import models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "approval.mixin"]

    def action_confirm(self):
        proceed = self.browse()
        for order in self:
            if order._approval_request_or_proceed():
                proceed |= order
        if proceed:
            return super(SaleOrder, proceed).action_confirm()
        return True

    def _approval_on_granted(self):
        return self.action_confirm()
