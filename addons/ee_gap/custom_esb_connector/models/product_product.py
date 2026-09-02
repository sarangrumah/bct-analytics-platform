# -*- coding: utf-8 -*-
"""ESB keys on the Odoo product.

Follows the repo convention for fields added to core models: an ``x_<slug>_``
prefix, and the external identifier stored as a field on the target record
rather than in a side mapping table (same shape as ``x_sap_external_id`` in
``custom_finance_portal_sap``).

``x_esb_product_detail_id`` mirrors the *stock unit* detail, which is the one
opname and stock snapshots transact in. The full per-unit set lives in
``custom.esb.product.detail``.
"""

from __future__ import annotations

from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    x_esb_product_id = fields.Integer(string="ESB Product ID", index=True, copy=False)
    x_esb_product_code = fields.Char(string="ESB Product Code", index=True, copy=False)
    x_esb_product_detail_id = fields.Integer(
        string="ESB Product Detail ID (Stock Unit)",
        index=True,
        copy=False,
        help="The productDetailID in the product's stock unit — the key ESB "
        "transactional endpoints expect. Set by the ESB master sync.",
    )
    x_esb_detail_ids = fields.One2many("custom.esb.product.detail", "product_id", string="ESB Product Details")
    x_esb_synced_at = fields.Datetime(string="ESB Last Synced", readonly=True, copy=False)

    def _esb_detail_id(self, kind="stock"):
        """productDetailID to use for this product in ``kind`` unit."""
        self.ensure_one()
        if kind == "stock" and self.x_esb_product_detail_id:
            return self.x_esb_product_detail_id
        return self.env["custom.esb.product.detail"]._detail_for(self, kind).esb_product_detail_id
