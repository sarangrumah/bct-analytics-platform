# -*- coding: utf-8 -*-
"""``wms.barcode.mixin`` — barcode rendering for any WMS document model.

Inherit it on a model and its QWeb template can call
``o._wms_barcode_src(o.name)`` for a transaction-level barcode, or
``o._wms_barcode_src(line.product_id.barcode)`` per line, without the report
engine needing an HTTP round trip. The PNG bytes are also what the XLSX
exporters embed, so a report looks the same in both output formats.
"""

from __future__ import annotations

from odoo import models

from .wms_barcode import wms_barcode_data_uri, wms_barcode_png, wms_barcode_url


class WmsBarcodeMixin(models.AbstractModel):
    _name = "wms.barcode.mixin"
    _description = "WMS Barcode Rendering Helpers"

    def _wms_barcode_png(
        self,
        value: str,
        barcode_type: str = "Code128",
        width: int = 600,
        height: int = 100,
        humanreadable: bool = False,
    ) -> bytes:
        """Raw PNG bytes for ``value`` (empty bytes when unrenderable)."""
        return wms_barcode_png(self.env, value, barcode_type, width, height, humanreadable)

    def _wms_barcode_src(
        self,
        value: str,
        barcode_type: str = "Code128",
        width: int = 600,
        height: int = 100,
        humanreadable: bool = False,
    ) -> str:
        """``<img t-att-src="...">`` payload as a self-contained data URI."""
        return wms_barcode_data_uri(self.env, value, barcode_type, width, height, humanreadable)

    def _wms_barcode_url(
        self,
        value: str,
        barcode_type: str = "Code128",
        width: int = 600,
        height: int = 100,
        humanreadable: bool = False,
    ) -> str:
        """Legacy ``/report/barcode/...`` URL form, kept for existing templates."""
        return wms_barcode_url(value, barcode_type, width, height, humanreadable)

    # ------------------------------------------------------------------
    # Line-item helper
    # ------------------------------------------------------------------
    def _wms_item_barcode_value(self, product, lot=None) -> str:
        """The payload a line-item barcode should carry.

        Prefers the lot/serial name when the line is tracked (that is what the
        handheld scans back), else the product EAN, else its internal
        reference so the sheet is still keyable.
        """
        if lot and lot.name:
            return lot.name
        if not product:
            return ""
        return product.barcode or product.default_code or ""

    def _wms_item_barcode_src(self, value, width: int = 420, height: int = 70) -> str:
        """Data URI for a *line-item* barcode.

        Rendered as EAN-8/EAN-13 whenever the payload is one (``auto`` lets
        reportlab pick by length) because handhelds are routinely shipped with
        Code128 decoding switched off — see the Denso BHT units on this
        project. Anything else, and any payload EAN rendering rejects, falls
        back to Code128.
        """
        return self._wms_barcode_src(value, "auto", width, height) or self._wms_barcode_src(
            value, "Code128", width, height
        )
