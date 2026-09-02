# -*- coding: utf-8 -*-
"""Spot-check sampling for the cycle-count start wizard.

``spot_check`` draws a small random sample: at most
``custom_wms_reports.spot_check_sample_size`` quants (default 10), further
capped by the plan/wizard target like every other method.
"""

from __future__ import annotations

import random

from odoo import models

DEFAULT_SPOT_CHECK_SAMPLE = 10


class CycleCountStartWizard(models.TransientModel):
    _inherit = "custom.cycle.count.start.wizard"

    def _build_seed_lines(self, plan, limit: int):
        if plan.method != "spot_check":
            return super()._build_seed_lines(plan, limit)

        sample_size = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("custom_wms_reports.spot_check_sample_size", DEFAULT_SPOT_CHECK_SAMPLE)
        )
        limit = min(limit, sample_size) if limit else sample_size

        Quant = self.env["stock.quant"]
        domain = [("location_id.usage", "=", "internal")]
        if plan.scope_zone_ids:
            domain.append(("location_id", "child_of", plan.scope_zone_ids.ids))
        if plan.warehouse_id and plan.warehouse_id.view_location_id:
            domain.append(("location_id", "child_of", plan.warehouse_id.view_location_id.id))
        quants = Quant.search(domain)
        if not quants:
            return []
        ids = random.sample(quants.ids, k=min(limit, len(quants)))
        quants = Quant.browse(ids)
        return [
            {
                "sequence": seq * 10,
                "location_id": q.location_id.id,
                "product_id": q.product_id.id,
                "lot_id": q.lot_id.id if q.lot_id else False,
                "expected_qty": q.quantity or 0.0,
            }
            for seq, q in enumerate(quants, start=1)
        ]
