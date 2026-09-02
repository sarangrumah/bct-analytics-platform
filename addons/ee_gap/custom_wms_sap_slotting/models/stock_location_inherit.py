# -*- coding: utf-8 -*-
"""stock.location — SAP bin attributes.

``custom_wms_putaway`` already gives a bin its geometry (``wms_*_mm``), weight
ceiling and walk order. What it has no concept of is *which class of bin this
is* — the two routing dimensions the SAP storage search walks:

* ``wms_storage_type_id``    — Lagertyp: footwear racking, apparel shelving, floor
* ``wms_storage_section_id`` — Lagerbereich: the sport / end-use zone

A bin with neither set is invisible to the SAP search, which is how damage and
stock-count locations stay out of putaway without needing an exclusion list.

``wms_volume_ccm`` is cubic centimetres, matching the source bin master. The
engine works in cm3 throughout for the SAP path; the native ``product.volume``
field is m3 and is only used as a fallback (see ``_sap_capacity_ccm``).
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .wms_storage_type import BIN_TYPES


class StockLocation(models.Model):
    _inherit = "stock.location"

    wms_storage_type_id = fields.Many2one(
        "custom.wms.storage.type",
        string="Storage Type",
        index=True,
        help="SAP Lagertyp. Bins with no storage type are excluded from the SAP storage search.",
    )
    wms_storage_section_id = fields.Many2one(
        "custom.wms.storage.section",
        string="Storage Section",
        index=True,
        help="SAP Lagerbereich. Bins with no storage section are excluded from the SAP storage search.",
    )
    wms_bin_type = fields.Selection(BIN_TYPES, string="Bin Type")
    wms_warehouse_number = fields.Char(
        string="Warehouse Number",
        help="SAP Lgnum of the warehouse this bin belongs to, e.g. W07. Informational.",
    )
    wms_sap_bin_code = fields.Char(
        string="SAP Bin Code",
        index=True,
        help="Bin code as it appears in the SAP bin master, e.g. L01-0001-A.",
    )
    wms_volume_ccm = fields.Float(
        string="Bin Volume (cm3)",
        default=0.0,
        help="Usable volume of the bin in cubic centimetres. Falls back to length x width x height when left at zero.",
    )

    @api.constrains("wms_volume_ccm")
    def _check_wms_volume_ccm(self):
        for rec in self:
            if (rec.wms_volume_ccm or 0.0) < 0.0:
                raise ValidationError(_("Location %s: bin volume cannot be negative.") % rec.display_name)

    def _sap_capacity_ccm(self) -> float:
        """Bin capacity in cm3. ``0.0`` means "unknown" -> treated as unlimited.

        Prefers the explicit figure from the bin master over the derived one:
        the two disagree whenever a bin is only partly usable, and the master is
        the number the warehouse actually planned against.
        """
        self.ensure_one()
        if (self.wms_volume_ccm or 0.0) > 0.0:
            return self.wms_volume_ccm
        length, width, height = self._wms_dims_mm()
        if length and width and height:
            return (length / 10.0) * (width / 10.0) * (height / 10.0)
        return 0.0
