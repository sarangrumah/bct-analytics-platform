# -*- coding: utf-8 -*-
"""Mapping an ESB location onto an Odoo ``stock.location``.

``custom_wms_cycle_count`` lines require a real ``stock.location``, so counting
an ESB location needs a counterpart record. The connector deliberately does not
create these (Odoo does not own ESB stock, and auto-creating warehouses from a
cron would generate sequences and picking types nobody uses) — so the mapping is
an explicit, idempotent action here.

The created locations are *scratch* locations: they exist to anchor count lines
and are never used to move stock. They live under one clearly-named view
location so nobody mistakes them for operational warehouse structure.
"""

from __future__ import annotations

from odoo import api, models

ROOT_LOCATION_NAME = "ESB Outlets (counting only)"


class EsbLocation(models.Model):
    _inherit = "custom.esb.location"

    def action_create_odoo_location(self):
        """Create (or re-link) the Odoo location used to anchor count lines."""
        Location = self.env["stock.location"].sudo()
        root = self._esb_root_location()
        for rec in self:
            if rec.location_id:
                continue
            existing = Location.search([("location_id", "=", root.id), ("name", "=", rec.display_name)], limit=1)
            rec.location_id = existing or Location.create(
                {
                    "name": rec.display_name,
                    "location_id": root.id,
                    "usage": "internal",
                    "company_id": rec.company_id.id or self.env.company.id,
                }
            )
        return True

    @api.model
    def _esb_root_location(self):
        Location = self.env["stock.location"].sudo()
        root = Location.search([("name", "=", ROOT_LOCATION_NAME), ("usage", "=", "view")], limit=1)
        if root:
            return root
        # The name carries the warning: Odoo 19 dropped stock.location.comment,
        # and this tree must never be mistaken for operational structure.
        return Location.create(
            {
                "name": ROOT_LOCATION_NAME,
                "usage": "view",
                "company_id": self.env.company.id,
            }
        )
