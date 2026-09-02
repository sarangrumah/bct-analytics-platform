# -*- coding: utf-8 -*-
"""stock.location — inbound / QC quarantine flags."""

from __future__ import annotations

from odoo import api, fields, models


class StockLocation(models.Model):
    _inherit = "stock.location"

    wms_is_qc_area = fields.Boolean(
        string="Inbound / QC Area",
        default=False,
        help="Goods here are awaiting inspection. Implies 'Block Outbound "
        "Reservation' unless that flag is explicitly cleared.",
    )
    wms_block_reservation = fields.Boolean(
        string="Block Outbound Reservation",
        default=False,
        index=True,
        help="Stock in this location (and its children) is invisible to "
        "outbound reservation. Internal transfers that release the goods "
        "still see it.",
    )

    @api.onchange("wms_is_qc_area")
    def _onchange_wms_is_qc_area(self):
        for rec in self:
            if rec.wms_is_qc_area:
                rec.wms_block_reservation = True

    @api.model
    def _wms_blocked_location_ids(self) -> list[int]:
        """All location ids under any reservation-blocking location.

        Blocking is inherited by children: flagging the ``WH/Input`` view is
        enough, every bin beneath it is quarantined too. The result is cached
        per environment because the reservation path calls this for every
        single move line.
        """
        cache = self.env.cr.cache.setdefault("wms_blocked_locations", {})
        key = "ids"
        if key in cache:
            return cache[key]
        roots = self.sudo().search([("wms_block_reservation", "=", True)])
        ids = self.sudo().search([("id", "child_of", roots.ids)]).ids if roots else []
        cache[key] = ids
        return ids

    # -- cache invalidation ------------------------------------------------

    def _wms_clear_blocked_cache(self):
        self.env.cr.cache.pop("wms_blocked_locations", None)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._wms_clear_blocked_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {"wms_block_reservation", "wms_is_qc_area", "location_id", "active"} & set(vals):
            self._wms_clear_blocked_cache()
        return res

    def unlink(self):
        self._wms_clear_blocked_cache()
        return super().unlink()
