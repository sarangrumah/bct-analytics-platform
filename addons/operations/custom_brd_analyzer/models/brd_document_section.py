# -*- coding: utf-8 -*-
"""Structured section of an extracted BRD."""

from __future__ import annotations

from odoo import fields, models


class BrdDocumentSection(models.Model):
    _name = "brd.document.section"
    _description = "BRD Document Section"
    _order = "document_id, sequence, id"

    document_id = fields.Many2one("brd.document", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10, index=True)
    title = fields.Char(required=True)
    content = fields.Text()
    parent_section_id = fields.Many2one("brd.document.section", ondelete="set null")
    level = fields.Integer(default=1)
    page_or_slide = fields.Integer(string="Page / Slide")

    def name_get(self):
        return [(rec.id, f"{rec.sequence:03d} · {rec.title}") for rec in self]
