# -*- coding: utf-8 -*-
"""Barcode support on the count session, for the Stock Take / Spot Check PDF.

The printed count sheet is scanned twice: once at the top to open the session
on the handheld, then line by line as the counter works the bin. Both levels
come from ``wms.barcode.mixin``.
"""

from odoo import models


class CycleCountSession(models.Model):
    _name = "custom.cycle.count.session"
    _inherit = ["custom.cycle.count.session", "wms.barcode.mixin"]

    def _wms_count_line_barcode(self, line) -> str:
        """Line-item barcode payload: the lot when tracked, else the SKU."""
        return self._wms_item_barcode_value(line.product_id, line.lot_id)
