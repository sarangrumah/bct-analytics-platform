# -*- coding: utf-8 -*-
"""Reusable starting points for new articles.

A template is just a (category, html body) pair. Applying it overwrites
``knowledge.article.body`` with ``body_template``.
"""

from odoo import fields, models


class KnowledgeArticleTemplate(models.Model):
    _name = "knowledge.article.template"
    _description = "Knowledge Article Template"
    _order = "category, name"

    name = fields.Char(required=True)
    category = fields.Selection(
        [
            ("meeting_notes", "Meeting Notes"),
            ("project_brief", "Project Brief"),
            ("sop", "SOP"),
            ("runbook", "Runbook"),
            ("onboarding", "Onboarding"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
    )
    body_template = fields.Html(sanitize=True, translate=True)
    is_active = fields.Boolean(default=True)
    description = fields.Char(help="Short hint shown in the template picker.")
