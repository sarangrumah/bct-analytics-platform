# -*- coding: utf-8 -*-
"""Extend custom.ppob.provider.sku.map with the Oracle KODE_VOUCHER mapping."""

from odoo import fields, models


class PpobProviderSkuMap(models.Model):
    _inherit = "custom.ppob.provider.sku.map"

    oracle_kode_voucher = fields.Char(
        string="Oracle KODE_VOUCHER",
        index=True,
        help="Maps to MSG017T.KODE_VOUCHER. Required when the provider operates "
        "in bridge_mode=oracle_bridge. Used to translate the Odoo product "
        "to the voucher code for SP SellWithDenom_HA and back on ingest.",
    )
