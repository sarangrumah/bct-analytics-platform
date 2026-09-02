# -*- coding: utf-8 -*-
"""Per-section AI analysis output."""

from __future__ import annotations

from odoo import fields, models


class BrdAnalysis(models.Model):
    _name = "brd.analysis"
    _description = "BRD Section Analysis"
    _order = "document_id, section_id"

    document_id = fields.Many2one("brd.document", required=True, ondelete="cascade", index=True)
    section_id = fields.Many2one("brd.document.section", required=True, ondelete="cascade", index=True)

    capability_required = fields.Text()
    capabilities_mentioned = fields.Json(default=list)
    mapped_module_ids = fields.Many2many(
        "custom.module.capability.entry",
        "brd_analysis_mapped_module_rel",
        "analysis_id",
        "module_id",
        string="Mapped Hub Modules",
    )

    fit_score = fields.Integer(default=0, help="0-100 confidence that the listed modules cover the requirement.")
    gap_status = fields.Selection(
        [
            ("covered", "Covered"),
            ("partial", "Partial"),
            ("missing", "Missing"),
            ("unclear", "Unclear"),
        ],
        default="unclear",
        index=True,
    )
    gap_severity = fields.Selection(
        [
            ("must_have", "Must Have"),
            ("should_have", "Should Have"),
            ("nice_to_have", "Nice to Have"),
        ],
        default="should_have",
        index=True,
    )
    notes = fields.Text()
