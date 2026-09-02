# -*- coding: utf-8 -*-
"""product.template extension — ABC velocity + default handling unit.

The putaway engine scores on *packaging* geometry, not on the bare product:
what physically occupies a bin is the carton/pallet the goods arrive in.
Odoo 19 models that as ``stock.package.type`` (fields ``packaging_length``,
``width``, ``height``, ``base_weight``, ``max_weight``).

``wms_package_type_id`` is the per-product default used when a move line is
not yet packed into a concrete ``stock.package``. It is intentionally optional
— when unset the engine degrades to ``product.volume`` / ``product.weight``.
"""

from __future__ import annotations

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    abc_class = fields.Selection(
        [("A", "A"), ("B", "B"), ("C", "C")],
        string="ABC Class",
        default="B",
        help="Velocity-based ABC classification: A=fast, B=medium, C=slow.",
    )
    wms_package_type_id = fields.Many2one(
        "stock.package.type",
        string="Default Handling Unit",
        help="Package type used by the putaway engine to derive PxLxT and "
        "weight when the goods are not yet packed. Optional.",
    )
    wms_units_per_package = fields.Float(
        string="Units per Handling Unit",
        default=1.0,
        help="How many sellable units fit in one handling unit. Used to convert "
        "a move-line quantity into a package count for dimension scoring.",
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    abc_class = fields.Selection(related="product_tmpl_id.abc_class", store=True, readonly=False)
    wms_package_type_id = fields.Many2one(related="product_tmpl_id.wms_package_type_id", store=True, readonly=False)
    wms_units_per_package = fields.Float(related="product_tmpl_id.wms_units_per_package", store=True, readonly=False)

    # ------------------------------------------------------------------
    # Handling-unit resolution
    # ------------------------------------------------------------------

    def _wms_package_type(self, move_line=None):
        """Resolve the handling unit that applies to this product.

        Resolution order — most concrete first:
          1. the package the move line is actually going into;
          2. the product's configured default handling unit;
          3. nothing (caller falls back to product.volume / product.weight).
        """
        self.ensure_one()
        if move_line is not None and move_line:
            pkg = getattr(move_line, "result_package_id", False)
            ptype = getattr(pkg, "package_type_id", False) if pkg else False
            if ptype:
                return ptype
        return self.wms_package_type_id

    def _wms_package_dims_mm(self, move_line=None) -> tuple[float, float, float]:
        """(length, width, height) in mm of one handling unit. Zeros = unknown."""
        self.ensure_one()
        ptype = self._wms_package_type(move_line=move_line)
        if not ptype:
            return (0.0, 0.0, 0.0)
        return (
            ptype.packaging_length or 0.0,
            ptype.width or 0.0,
            ptype.height or 0.0,
        )

    def _wms_package_count(self, qty: float) -> float:
        """Number of handling units needed for ``qty`` sellable units."""
        self.ensure_one()
        per = self.wms_units_per_package or 0.0
        if per <= 0.0:
            return qty
        return qty / per

    def _wms_gross_weight_kg(self, qty: float, move_line=None) -> float:
        """Gross weight of ``qty`` units including handling-unit tare."""
        self.ensure_one()
        net = (self.weight or 0.0) * (qty or 0.0)
        ptype = self._wms_package_type(move_line=move_line)
        if not ptype:
            return net
        tare = (ptype.base_weight or 0.0) * self._wms_package_count(qty or 0.0)
        return net + tare
