# -*- coding: utf-8 -*-
"""Storage Section (SAP Lagerbereich) and its ordered search sequence.

Where the storage type says *what kind of bin*, the section says *which part of
the hall* — in this configuration the sport / end-use zone: Run, Train, Golf,
Basketball, and the catch-all General (``GA2``).

Each section carries its own ordered fallback list. Note the shape of the
reference data: every sport falls back to ``GA2`` second, then rotates through
the remaining sports. ``GA2`` itself searches only ``GA2`` — general stock never
invades a dedicated sport zone.

See :mod:`wms_storage_type` for why these are dedicated models rather than
native ``stock.storage.category`` records.
"""

from __future__ import annotations

from odoo import api, fields, models


class WmsStorageSection(models.Model):
    _name = "custom.wms.storage.section"
    _description = "WMS Storage Section (SAP Lagerbereich)"
    _order = "sequence, code"

    code = fields.Char(required=True, index=True, help="SAP storage section code, e.g. RU1.")
    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    is_general = fields.Boolean(
        string="General Section",
        help="The catch-all section every other section falls back to.",
    )
    active = fields.Boolean(default=True)
    search_line_ids = fields.One2many(
        "custom.wms.storage.section.search.line",
        "section_id",
        string="Search Sequence",
    )

    _uniq_code = models.Constraint("UNIQUE(code)", "Storage section code must be unique.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.code} {rec.name}" if rec.code else (rec.name or "")

    def _search_sequence(self):
        """Ordered recordset of sections to try, most preferred first.

        Never empty — see ``custom.wms.storage.type._search_sequence``.
        """
        self.ensure_one()
        result = self.browse()
        for line in self.search_line_ids.sorted(key=lambda line: (line.sequence, line.id)):
            if line.target_section_id:
                result |= line.target_section_id
        if self not in result:
            result = self | result
        return result


class WmsStorageSectionSearchLine(models.Model):
    _name = "custom.wms.storage.section.search.line"
    _description = "WMS Storage Section Search Sequence Line"
    _order = "sequence, id"

    section_id = fields.Many2one(
        "custom.wms.storage.section",
        string="Storage Section",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    target_section_id = fields.Many2one(
        "custom.wms.storage.section",
        string="Search",
        required=True,
        ondelete="cascade",
    )

    _uniq_line = models.Constraint(
        "UNIQUE(section_id, target_section_id)",
        "A storage section may appear only once in a search sequence.",
    )
