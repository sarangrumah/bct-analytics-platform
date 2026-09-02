# -*- coding: utf-8 -*-
"""Suppress Odoo stock moves for ESB-backed counts.

``custom_wms_cycle_count`` posts each approved variance as a ``stock.move``
against the inventory-loss location. That is right when Odoo owns the stock —
and wrong here. For an EFN outlet the stock lives in ESB, and the adjustment is
made by the Item Journal the session emits on close. Creating an Odoo move as
well would fabricate a movement (and, on a valued product, a journal entry) for
inventory Odoo does not hold.

The adjustment record is still created and still marked posted, so the audit
trail and the supervisor approval history stay intact; only the phantom move is
skipped.
"""

from __future__ import annotations

from odoo import models


class CycleCountAdjustment(models.Model):
    _inherit = "custom.cycle.count.adjustment"

    def action_post(self):
        esb_backed = self.filtered(lambda a: a.line_id.session_id.is_esb_backed)
        if esb_backed:
            esb_backed.write({"posted": True})
        remainder = self - esb_backed
        if remainder:
            return super(CycleCountAdjustment, remainder).action_post()
        return True
