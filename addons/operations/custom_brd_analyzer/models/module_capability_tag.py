# -*- coding: utf-8 -*-
"""Capability tagging vocabulary.

Tags are seeded via XML but admins are free to add more. They are used both
by the auto-scanner (manifest keyword → tag) and by the AI analyzer (the
prompt asks the LLM to return tags from this vocabulary).
"""

from __future__ import annotations

from odoo import fields, models


class CustomModuleCapabilityTag(models.Model):
    _name = "custom.module.capability.tag"
    _description = "Module Capability Tag"
    _order = "name asc"

    name = fields.Char(required=True, translate=False, index=True)
    technical_code = fields.Char(
        required=True,
        index=True,
        help="Stable machine-readable identifier (e.g. 'rental'). "
        "This is what the AI will emit; do not rename casually.",
    )
    description = fields.Text()
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    _technical_code_uniq = models.Constraint(
        "unique(technical_code)",
        "Capability tag technical_code must be unique.",
    )
