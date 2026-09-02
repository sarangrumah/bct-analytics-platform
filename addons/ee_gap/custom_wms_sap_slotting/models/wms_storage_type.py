# -*- coding: utf-8 -*-
"""Storage Type (SAP Lagertyp) and its ordered search sequence.

A storage type classifies *what kind of goods a bin is built for* — footwear
racking, apparel shelving, the half-height "HD" levels, the floor. Putaway does
not simply reject a bin of the wrong type: SAP defines, per type, an ordered
list of types to try. ``FO1`` (footwear) falls back to ``FO2`` (its own
half-height levels), then borrows apparel and accessory shelving, and finally
lands on the floor.

That ordering is data, not code — it differs per site and changes when racking
is re-purposed — so it lives in ``search_line_ids`` and is shipped as module
data that a ``-u`` can correct.

Why not ``stock.storage.category``? That native model is already the
authoritative source for weight ceilings and per-package-type unit counts, and
``custom.putaway.engine._native_capacity_free`` reads it. Storage type is an
orthogonal routing dimension; overloading the native model would break capacity.
"""

from __future__ import annotations

from odoo import api, fields, models

BIN_TYPES = [
    ("SH", "Shelving"),
    ("RA", "Racking"),
    ("FL", "Flooring"),
]


class WmsStorageType(models.Model):
    _name = "custom.wms.storage.type"
    _description = "WMS Storage Type (SAP Lagertyp)"
    _order = "sequence, code"

    code = fields.Char(required=True, index=True, help="SAP storage type code, e.g. FO1.")
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    bin_type = fields.Selection(BIN_TYPES, string="Bin Type")
    is_high_density = fields.Boolean(
        string="High Density",
        help="Half-height overflow levels (the SAP 'HD' types). Reachable only "
        "as a fallback — no product is classified into one directly.",
    )
    active = fields.Boolean(default=True)
    search_line_ids = fields.One2many(
        "custom.wms.storage.type.search.line",
        "type_id",
        string="Search Sequence",
    )

    _uniq_code = models.Constraint("UNIQUE(code)", "Storage type code must be unique.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.code} {rec.name}" if rec.code else (rec.name or "")

    def _search_sequence(self):
        """Ordered recordset of storage types to try, most preferred first.

        Never empty: a type with no configured sequence still searches itself,
        so a half-configured site degrades to "own type only" rather than to
        "nowhere at all".
        """
        self.ensure_one()
        result = self.browse()
        for line in self.search_line_ids.sorted(key=lambda line: (line.sequence, line.id)):
            if line.target_type_id:
                result |= line.target_type_id
        if self not in result:
            result = self | result
        return result


class WmsStorageTypeSearchLine(models.Model):
    _name = "custom.wms.storage.type.search.line"
    _description = "WMS Storage Type Search Sequence Line"
    _order = "sequence, id"

    type_id = fields.Many2one(
        "custom.wms.storage.type",
        string="Storage Type",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    target_type_id = fields.Many2one(
        "custom.wms.storage.type",
        string="Search",
        required=True,
        ondelete="cascade",
    )

    _uniq_line = models.Constraint(
        "UNIQUE(type_id, target_type_id)",
        "A storage type may appear only once in a search sequence.",
    )
