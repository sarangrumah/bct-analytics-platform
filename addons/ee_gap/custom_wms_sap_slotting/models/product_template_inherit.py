# -*- coding: utf-8 -*-
"""product.template — the product side of the SAP storage search.

The search needs two classifications per SKU: which storage type it belongs in
(derived from the merchandise category — Footwear/Apparel/Accessory) and which
storage section (derived from its end use — Run/Train/Golf/...). They are stored
as real m2o fields rather than derived on the fly because the source master
carries them per SKU and they are edited per SKU when slotting is re-planned.

Physical size is kept in cm3 / mm to match the source data. The one behavioural
override here is ``_wms_package_dims_mm``: without it the geometry gate in
``custom.putaway.engine._fits_dimensions`` is inert for this catalogue, because
these SKUs have per-piece dimensions but no ``stock.package.type``.
"""

from __future__ import annotations

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    wms_storage_type_id = fields.Many2one(
        "custom.wms.storage.type",
        string="Storage Type",
        help="SAP Lagertyp this product is slotted into. Usually derived from the merchandise category.",
    )
    wms_storage_section_id = fields.Many2one(
        "custom.wms.storage.section",
        string="Storage Section",
        help="SAP Lagerbereich this product is slotted into. Usually derived from the end use.",
    )

    # -- physical size (source master is cm; kept as cm3 + mm) -------------
    wms_volume_ccm = fields.Float(string="Piece Volume (cm3)", default=0.0)
    wms_piece_length_mm = fields.Float(string="Piece Length (mm)", default=0.0)
    wms_piece_width_mm = fields.Float(string="Piece Width (mm)", default=0.0)
    wms_piece_height_mm = fields.Float(string="Piece Height (mm)", default=0.0)

    # -- source-system attributes -----------------------------------------
    sap_old_material_num = fields.Char(string="Old Material Number", index=True)
    sap_brand_code = fields.Char(string="Brand Code")
    sap_category_code = fields.Char(string="Category Code")
    wms_specs = fields.Char(string="Specs")
    wms_gender = fields.Char(string="Gender")
    wms_size = fields.Char(string="Size")


class ProductProduct(models.Model):
    _inherit = "product.product"

    wms_storage_type_id = fields.Many2one(related="product_tmpl_id.wms_storage_type_id", store=True, readonly=False)
    wms_storage_section_id = fields.Many2one(
        related="product_tmpl_id.wms_storage_section_id", store=True, readonly=False
    )
    wms_volume_ccm = fields.Float(related="product_tmpl_id.wms_volume_ccm", store=True, readonly=False)
    wms_piece_length_mm = fields.Float(related="product_tmpl_id.wms_piece_length_mm", store=True, readonly=False)
    wms_piece_width_mm = fields.Float(related="product_tmpl_id.wms_piece_width_mm", store=True, readonly=False)
    wms_piece_height_mm = fields.Float(related="product_tmpl_id.wms_piece_height_mm", store=True, readonly=False)

    def _wms_package_dims_mm(self, move_line=None) -> tuple[float, float, float]:
        """Handling-unit dimensions, falling back to the bare piece.

        The base implementation returns zeros when no ``stock.package.type`` is
        resolved, which makes ``_fits_dimensions`` permissive. For a catalogue
        loaded from a master that carries per-piece PxLxT but no packaging, that
        silently disables the geometry constraint — so use the piece as a
        one-unit handling unit instead.
        """
        self.ensure_one()
        dims = super()._wms_package_dims_mm(move_line=move_line)
        if any(dims):
            return dims
        return (
            self.wms_piece_length_mm or 0.0,
            self.wms_piece_width_mm or 0.0,
            self.wms_piece_height_mm or 0.0,
        )

    def _sap_volume_ccm(self) -> float:
        """Volume of one sellable unit in cm3, or 0.0 when unknown.

        ``product.volume`` is m3 in Odoo; the SAP path works in cm3 end to end,
        so the fallback conversion happens here and nowhere else.
        """
        self.ensure_one()
        if (self.wms_volume_ccm or 0.0) > 0.0:
            return self.wms_volume_ccm
        return (self.volume or 0.0) * 1_000_000.0
