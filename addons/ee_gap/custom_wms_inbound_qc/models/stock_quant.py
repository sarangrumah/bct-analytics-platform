# -*- coding: utf-8 -*-
"""stock.quant — hide quarantined stock from reservation.

Odoo resolves *everything* reservation-related through ``_get_gather_domain``:
``_gather`` -> ``_get_available_quantity`` -> ``_get_reserve_quantity`` ->
``_update_reserve_quantity``, plus the forecast widgets. Filtering there is the
single choke point; overriding ``_gather`` alone would leave the availability
computations reading quarantined stock.

The block is lifted by the context key ``wms_allow_blocked_locations``, which
the QC release transfer sets — otherwise the release move itself could never
pick the goods up out of quarantine.
"""

from __future__ import annotations

from odoo import api, models
from odoo.fields import Domain

#: Context flag that lifts the quarantine filter for a single operation.
BYPASS_CTX = "wms_allow_blocked_locations"


class StockQuant(models.Model):
    _inherit = "stock.quant"

    @api.model
    def _get_gather_domain(self, product_id, location_id, lot_id=None, package_id=None, owner_id=None, strict=False):
        domain = super()._get_gather_domain(
            product_id, location_id, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=strict
        )
        if self.env.context.get(BYPASS_CTX):
            return domain
        blocked = self.env["stock.location"]._wms_blocked_location_ids()
        if not blocked:
            return domain
        return Domain.AND([domain, Domain("location_id", "not in", blocked)])
