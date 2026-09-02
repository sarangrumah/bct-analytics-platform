# -*- coding: utf-8 -*-
"""Serial-tracking extension of ``custom.fixed.asset``.

The base model has no serial or source-listing fields (one record = one asset).
The tenant drone register is loaded per unit/serial, so we add a small set of
identity fields to hold the physical listing's serial number, asset group and
description. These are informational only -- they do not affect depreciation.
"""

from odoo import api, fields, models


class CustomFixedAssetGroup(models.Model):
    _inherit = "custom.fixed.asset.group"

    @api.model
    def _seed_aim_asset_register(self):
        """Data-hook entry point: upsert the tenant asset group + location.

        Delegates to ``hooks.seed_group_and_location`` (lazy import to avoid a
        circular import at module load). Idempotent -> safe to re-run on every
        module upgrade.
        """
        from ..hooks import seed_group_and_location

        seed_group_and_location(self.env)
        return True


class CustomFixedAsset(models.Model):
    _inherit = "custom.fixed.asset"

    serial_number = fields.Char(
        index=True,
        copy=False,
        help="Physical serial number from the asset listing (blank for spares/consumables that carry no serial).",
    )
    source_group = fields.Char(
        string="Source Asset Group",
        help="Asset Group as it appears in the source listing (provenance).",
    )
    source_desc = fields.Char(
        string="Source Description",
        help="Asset Description as it appears in the source listing (provenance).",
    )
