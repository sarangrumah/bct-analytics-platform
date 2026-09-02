# -*- coding: utf-8 -*-
"""stock.location extension — capacity, dimensions, walk order, category reservation.

Odoo 19 CE already ships the canonical capacity model:

    stock.location.storage_category_id -> stock.storage.category
        .max_weight          (kg)
        .allow_new_product   ('empty' / 'same' / 'mixed')
        .capacity_ids        -> stock.storage.category.capacity
                                  (per product OR per stock.package.type)

This module does NOT replace that. The fields below are *supplements* for the
two things the native model does not express:

* **Bin geometry** (``wms_length_mm`` / ``wms_width_mm`` / ``wms_height_mm``) —
  needed to test whether a physical package (PxLxT from ``stock.package.type``)
  actually fits the shelf opening. Native storage categories only count units,
  never dimensions.
* **Product-category reservation** (``wms_allowed_categ_ids``) — native putaway
  *routes* by category but does not *reserve* a bin against foreign categories.

``volume_capacity_m3`` predates the native model and is kept as a last-resort
fallback so existing configurations keep scoring.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockLocation(models.Model):
    _inherit = "stock.location"

    # -- legacy volumetrics (fallback only) -------------------------------
    volume_capacity_m3 = fields.Float(string="Volume Capacity (m3)", default=0.0)
    volume_used_m3 = fields.Float(
        string="Volume Used (m3)",
        compute="_compute_volume_used",
        store=False,
    )

    # -- bin geometry ------------------------------------------------------
    wms_length_mm = fields.Float(string="Bin Length (mm)", default=0.0)
    wms_width_mm = fields.Float(string="Bin Width (mm)", default=0.0)
    wms_height_mm = fields.Float(string="Bin Height (mm)", default=0.0)

    # -- weight ------------------------------------------------------------
    wms_max_weight_kg = fields.Float(
        string="Max Weight (kg)",
        default=0.0,
        help="Fallback weight ceiling used only when the location has no "
        "storage category (whose max_weight always wins).",
    )
    wms_weight_used_kg = fields.Float(
        string="Weight Used (kg)",
        compute="_compute_wms_weight_used",
        store=False,
    )

    # -- walk order --------------------------------------------------------
    wms_walk_sequence = fields.Integer(
        string="Walk Sequence",
        default=0,
        index=True,
        help="Position of this bin along the physical picking/putaway route. "
        "Distance between two bins is approximated as the absolute difference "
        "of their walk sequences. Leave 0 to fall back to lexical ordering on "
        "the complete name.",
    )

    # -- category reservation ---------------------------------------------
    wms_allowed_categ_ids = fields.Many2many(
        "product.category",
        "stock_location_allowed_categ_rel",
        "location_id",
        "categ_id",
        string="Reserved for Categories",
        help="If set, only products in these categories (or their children) may be slotted here by the putaway engine.",
    )
    wms_enforce_categ = fields.Boolean(
        string="Enforce Category Reservation",
        default=False,
        help="Also block manual moves into this location for products outside "
        "the reserved categories, not just engine suggestions.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("quant_ids", "quant_ids.quantity", "quant_ids.product_id")
    def _compute_volume_used(self):
        for rec in self:
            total = 0.0
            for q in rec.quant_ids:
                total += (q.product_id.volume or 0.0) * (q.quantity or 0.0)
            rec.volume_used_m3 = total

    @api.depends("quant_ids", "quant_ids.quantity", "quant_ids.product_id")
    def _compute_wms_weight_used(self):
        for rec in self:
            total = 0.0
            for q in rec.quant_ids:
                total += (q.product_id.weight or 0.0) * (q.quantity or 0.0)
            rec.wms_weight_used_kg = total

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("wms_length_mm", "wms_width_mm", "wms_height_mm", "wms_max_weight_kg")
    def _check_wms_dimensions(self):
        for rec in self:
            for fname, label in (
                ("wms_length_mm", _("length")),
                ("wms_width_mm", _("width")),
                ("wms_height_mm", _("height")),
                ("wms_max_weight_kg", _("max weight")),
            ):
                if (rec[fname] or 0.0) < 0.0:
                    raise ValidationError(
                        _("Location %(loc)s: %(label)s cannot be negative.") % {"loc": rec.display_name, "label": label}
                    )

    # ------------------------------------------------------------------
    # WMS helpers — consumed by custom.putaway.engine
    # ------------------------------------------------------------------

    def _wms_effective_max_weight(self) -> float:
        """Weight ceiling in kg. Native storage category wins; 0.0 = unlimited."""
        self.ensure_one()
        cat = self.storage_category_id
        if cat and (cat.max_weight or 0.0) > 0.0:
            return cat.max_weight
        return self.wms_max_weight_kg or 0.0

    def _wms_free_weight_kg(self) -> float:
        """Remaining kg, or ``float('inf')`` when no ceiling is configured."""
        self.ensure_one()
        ceiling = self._wms_effective_max_weight()
        if ceiling <= 0.0:
            return float("inf")
        return max(0.0, ceiling - (self.wms_weight_used_kg or 0.0))

    def _wms_dims_mm(self) -> tuple[float, float, float]:
        """Bin opening as (length, width, height) in mm. Zeros mean 'unknown'."""
        self.ensure_one()
        return (self.wms_length_mm or 0.0, self.wms_width_mm or 0.0, self.wms_height_mm or 0.0)

    def _wms_accepts_category(self, categ) -> bool:
        """True when ``categ`` may be slotted here.

        An empty ``wms_allowed_categ_ids`` means "no reservation" -> accepts all.
        Matching is ``child_of``: reserving a parent category reserves the tree.
        """
        self.ensure_one()
        allowed = self.wms_allowed_categ_ids
        if not allowed:
            return True
        if not categ:
            return False
        allowed_tree = self.env["product.category"].search([("id", "child_of", allowed.ids)])
        return categ.id in allowed_tree.ids

    def _wms_walk_distance_to(self, other) -> float:
        """Approximate distance between two bins along the walk route."""
        self.ensure_one()
        if not other:
            return float("inf")
        if self.wms_walk_sequence and other.wms_walk_sequence:
            return abs(self.wms_walk_sequence - other.wms_walk_sequence)
        # No walk order configured: fall back to lexical proximity of the
        # complete name, which mirrors how aisle/rack codes are laid out.
        a = self.complete_name or ""
        b = other.complete_name or ""
        common = 0
        for ca, cb in zip(a, b, strict=False):
            if ca != cb:
                break
            common += 1
        return float(max(len(a), len(b)) - common)
